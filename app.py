import math
import streamlit as st

# ==========================
# Utility / business logic
# ==========================

def compute_bmi(weight_kg: float, height_cm: float | None) -> float | None:
    if not height_cm or height_cm <= 0:
        return None
    h_m = height_cm / 100.0
    return weight_kg / (h_m ** 2)


def compute_effective_weight(weight_kg: float, height_cm: float | None) -> float:
    """
    If BMI >= 35, use adjusted/ideal weight based on BMI 25 to avoid over-dosing in obesity.
    Otherwise use actual weight.
    """
    bmi = compute_bmi(weight_kg, height_cm)
    if bmi is None or bmi < 35:
        return weight_kg

    h_m = height_cm / 100.0
    ideal_weight = 25 * (h_m ** 2)
    # Use a simple adjusted weight midway between ideal and actual to avoid under-dosing.
    adjusted_weight = ideal_weight + 0.4 * (weight_kg - ideal_weight)
    return max(ideal_weight, min(weight_kg, adjusted_weight))


def map_ata_risk_and_response(risk: str, response: str, years_since_surgery: float | None) -> str:
    """
    Return suppression level: "None", "Mild", "Moderate", "Strong"
    """
    # Normalize
    risk = (risk or "").lower()
    response = (response or "").lower()
    yrs = years_since_surgery or 0.0

    # Defaults (can be softened later based on risk/response/years)
    if risk == "low":
        if response == "excellent":
            return "None"
        elif response == "indeterminate":
            return "Mild"
        elif response == "biochemical incomplete":
            return "Mild"
        elif response == "structural incomplete":
            return "Strong"
        else:
            return "Mild"
    elif risk == "intermediate":
        if response == "excellent":
            return "Mild"
        elif response == "indeterminate":
            return "Mild"
        elif response == "biochemical incomplete":
            return "Moderate"
        elif response == "structural incomplete":
            return "Strong"
        else:
            return "Moderate"
    elif risk == "high":
        if response == "excellent":
            # In practice, often strong suppression in early years, later can be relaxed
            if yrs >= 5:
                return "Mild"
            else:
                return "Moderate"
        elif response == "indeterminate":
            return "Moderate"
        elif response == "biochemical incomplete":
            return "Strong"
        elif response == "structural incomplete":
            return "Strong"
        else:
            return "Moderate"
    else:
        # Unknown risk: choose conservative mild suppression
        return "Mild"


def soften_suppression_level(level: str, high_cv_risk: bool, high_bone_risk: bool) -> str:
    """
    If CV or bone risk is high, reduce suppression by one 'step' when possible.
    """
    if not (high_cv_risk or high_bone_risk):
        return level

    order = ["None", "Mild", "Moderate", "Strong"]
    idx = order.index(level) if level in order else 1
    # Step down by 1 if possible
    new_idx = max(0, idx - 1)
    return order[new_idx]


def suppression_to_tsh_range(level: str, scenario: str) -> tuple[float, float]:
    """
    Map suppression level to a numeric TSH target range (mIU/L).
    """
    level = level or "None"
    level = level.capitalize()

    if scenario == "A":  # Non-cancer hypothyroidism, replacement
        return 0.5, 2.5

    # Scenario B – DTC
    if level == "None":
        return 0.5, 2.0
    elif level == "Mild":
        return 0.1, 0.5
    elif level == "Moderate":
        return 0.1, 0.5
    elif level == "Strong":
        # We still define an "upper" bound for display, though goal is <0.1
        return 0.0, 0.1
    else:
        return 0.5, 2.0


def suppression_level_to_factor(level: str) -> float:
    """
    Multiplicative factor relative to base replacement dose.
    """
    level = level or "None"
    level = level.capitalize()
    if level == "None":
        return 1.0
    elif level == "Mild":
        return 1.15
    elif level == "Moderate":
        return 1.25
    elif level == "Strong":
        return 1.35
    else:
        return 1.0


def base_replacement_mcg_per_kg(age: int, high_cv_risk: bool) -> float:
    """
    Base mcg/kg/day before applying suppression factor.
    """
    if age < 60 and not high_cv_risk:
        return 1.6  # typical full replacement
    else:
        # elderly or CV disease – start more cautiously
        return 0.9


def build_safety_flags(
    age: int,
    sex: str,
    high_cv_risk: bool,
    high_bone_risk: bool,
    pregnancy_status: str,
    suggested_mcg_day: float,
    effective_weight_kg: float,
    current_tsh: float | None,
    scenario: str,
    suppression_level: str
) -> list[str]:
    flags = []

    if high_cv_risk:
        flags.append(
            "High cardiovascular risk – avoid aggressive suppression; "
            "consider endocrinology/cardiology input."
        )

    if high_bone_risk and suppression_level in ["Moderate", "Strong"]:
        flags.append(
            "High fracture risk – prolonged TSH <0.1 mIU/L may worsen bone loss; "
            "consider milder suppression and bone protection strategies."
        )

    if pregnancy_status != "Non-pregnant":
        flags.append(
            "Pregnancy/postpartum – use dedicated pregnancy thyroid guidelines and closer monitoring."
        )

    if effective_weight_kg > 0:
        mcg_per_kg = suggested_mcg_day / effective_weight_kg
        if mcg_per_kg > 2.2:
            flags.append(
                f"Dose >2.2 mcg/kg/day (≈{mcg_per_kg:.2f} mcg/kg) – consider malabsorption, "
                "drug interactions, or non-adherence before further escalation."
            )

    if current_tsh is not None and current_tsh < 0.1:
        flags.append(
            "TSH already strongly suppressed – weigh oncologic benefit vs atrial fibrillation "
            "and osteoporosis risk."
        )

    if scenario == "A" and suppression_level != "None":
        flags.append(
            "Non-cancer hypothyroidism – TSH suppression is usually not indicated; "
            "aim for replacement rather than suppression."
        )

    return flags


def calculate_lt4_and_targets(inputs: dict) -> dict:
    """
    Core calculator: takes a dict of inputs and returns computed outputs.
    """
    age = inputs["age"]
    sex = inputs["sex"]
    height_cm = inputs.get("height_cm")
    weight_kg = inputs["weight_kg"]
    pregnancy_status = inputs["pregnancy_status"]
    indication = inputs["indication"]
    current_tsh = inputs.get("current_tsh")
    current_lt4 = inputs.get("current_lt4")

    ischemic_hd = inputs.get("ischemic_hd", False)
    arrhythmia = inputs.get("arrhythmia", False)
    heart_failure = inputs.get("heart_failure", False)
    osteoporosis = inputs.get("osteoporosis", False)

    initial_ata_risk = inputs.get("initial_ata_risk")
    disease_status = inputs.get("disease_status")
    time_since_surgery_years = inputs.get("time_since_surgery_years")

    # Scenario classification
    scenario = "A" if indication == "Post-RAI for hyperthyroidism" else "B"

    high_cv_risk = (
        age >= 70
        or ischemic_hd
        or arrhythmia
        or heart_failure
    )
    high_bone_risk = osteoporosis or (sex == "Female" and age >= 55)

    # TSH target mapping
    if scenario == "A":
        suppression_level = "None"
    else:
        suppression_level = map_ata_risk_and_response(
            initial_ata_risk, disease_status, time_since_surgery_years
        )
        suppression_level = soften_suppression_level(
            suppression_level, high_cv_risk, high_bone_risk
        )

    tsh_low, tsh_high = suppression_to_tsh_range(suppression_level, scenario)

    # Base replacement & suppression factor
    effective_weight = compute_effective_weight(weight_kg, height_cm)
    base_mcg_per_kg = base_replacement_mcg_per_kg(age, high_cv_risk)
    base_mcg_day = base_mcg_per_kg * effective_weight

    factor = suppression_level_to_factor(suppression_level if scenario == "B" else "None")
    suggested_central_mcg_day = base_mcg_day * factor

    # Range around central dose (±10%)
    mcg_min = suggested_central_mcg_day * 0.9
    mcg_max = suggested_central_mcg_day * 1.1

    # Absolute caps
    ABS_MAX_MCG_DAY = 250
    if suggested_central_mcg_day > ABS_MAX_MCG_DAY:
        suggested_central_mcg_day = ABS_MAX_MCG_DAY
    mcg_min = min(mcg_min, suggested_central_mcg_day)
    mcg_max = max(mcg_max, suggested_central_mcg_day)

    # mcg/kg ranges
    if effective_weight > 0:
        mcg_kg_min = mcg_min / effective_weight
        mcg_kg_max = mcg_max / effective_weight
    else:
        mcg_kg_min = mcg_kg_max = 0

            # Dose adjustment suggestion (now considers current dose vs recommended range)
    dose_adjustment_suggestion = None
    if current_lt4 is not None and current_tsh is not None:
        # Where is the current dose relative to the recommended range?
        if current_lt4 < mcg_min - 1e-6:
            dose_position = "below"
        elif current_lt4 > mcg_max + 1e-6:
            dose_position = "above"
        else:
            dose_position = "within"

        if current_tsh > tsh_high:
            # TSH too high -> under-treated
            if dose_position == "below":
                delta = 12.5 if high_cv_risk else 25.0
                new_target = min(suggested_central_mcg_day, mcg_max)
                dose_adjustment_suggestion = (
                    f"TSH above target and current dose is below the recommended range – "
                    f"consider titrating up towards ~{new_target:.0f} mcg/day "
                    f"(e.g., increase by ~{delta:.1f} mcg/day)."
                )
            elif dose_position == "within":
                dose_adjustment_suggestion = (
                    "TSH above target despite a dose within the recommended range – "
                    "before further increasing LT4, consider adherence, absorption issues, "
                    "interacting drugs, or specialist endocrinology review."
                )
            else:  # dose_position == "above"
                dose_adjustment_suggestion = (
                    "TSH above target but current dose is already at/above the recommended range – "
                    "investigate malabsorption, non-adherence, lab error, or interfering medications "
                    "rather than automatically escalating the dose."
                )

        elif current_tsh < tsh_low:
            # TSH too low -> over-treated
            if dose_position == "above":
                delta = 12.5 if (high_cv_risk or high_bone_risk) else 25.0
                new_target = max(suggested_central_mcg_day, mcg_min)
                dose_adjustment_suggestion = (
                    f"TSH below target and current dose is above the recommended range – "
                    f"consider titrating down towards ~{new_target:.0f} mcg/day "
                    f"(e.g., reduce by ~{delta:.1f} mcg/day)."
                )
            elif dose_position == "within":
                delta = 12.5 if (high_cv_risk or high_bone_risk) else 25.0
                dose_adjustment_suggestion = (
                    f"TSH below target with dose within the recommended range – "
                    f"consider a cautious reduction (e.g., ~{delta:.1f} mcg/day) and re-check TSH."
                )
            else:  # dose_position == "below"
                dose_adjustment_suggestion = (
                    "TSH below target despite a dose already below the recommended range – "
                    "patient may be especially sensitive to LT4; consider endocrinology input and "
                    "avoid aggressive suppression."
                )
        else:
            dose_adjustment_suggestion = (
                "TSH within the chosen target range – routine dose change is not required. "
                "Continue current dose and monitor."
            )

    followup_weeks = 8 if high_cv_risk else 6

    safety_flags = build_safety_flags(
        age=age,
        sex=sex,
        high_cv_risk=high_cv_risk,
        high_bone_risk=high_bone_risk,
        pregnancy_status=pregnancy_status,
        suggested_mcg_day=suggested_central_mcg_day,
        effective_weight_kg=effective_weight,
        current_tsh=current_tsh,
        scenario=scenario,
        suppression_level=suppression_level,
    )

    # Build a short narrative note
    note_parts = []
    if scenario == "A":
        note_parts.append("Scenario A: Post-RAI hypothyroidism (non-DTC) – aim for physiologic replacement.")
    else:
        note_parts.append(f"Scenario B: DTC with ATA risk '{initial_ata_risk}' and response '{disease_status}'.")
        note_parts.append(f"Suppression level: {suppression_level}.")

    note = " ".join(note_parts)

    return {
        "scenario": scenario,
        "suppression_level": suppression_level,
        "tsh_target_range": (tsh_low, tsh_high),
        "lt4_recommended_mcg_day_range": (mcg_min, mcg_max),
        "lt4_recommended_mcg_kg_day_range": (mcg_kg_min, mcg_kg_max),
        "lt4_suggested_central_mcg_day": suggested_central_mcg_day,
        "dose_adjustment_suggestion": dose_adjustment_suggestion,
        "followup_TSH_interval_weeks": followup_weeks,
        "safety_flags": safety_flags,
        "special_note": note,
        "effective_weight_kg": effective_weight,
    }


# ==========================
# Streamlit UI
# ==========================

def main():
    st.set_page_config(
        page_title="Levothyroxine & TSH Target Calculator (Nuclear Medicine)",
        layout="wide",
    )

    st.title("Levothyroxine Dosing & TSH-Target Calculator")
    st.markdown(
        """
**For Nuclear Medicine Physicians – Decision-Support Only**

This tool is intended **only for qualified clinicians** to support dosing and TSH target decisions in:

1. Patients with **post-RAI hypothyroidism** after treatment of hyperthyroidism.
2. Patients with **differentiated thyroid cancer (DTC)** after total/near-total thyroidectomy ± RAI.

It does **not** replace endocrinology/oncology consultation, local protocols, or your clinical judgment.
"""
    )

    st.warning(
        "Not for patient self-management. Always interpret results in full clinical context "
        "and according to local guidelines."
    )

    # Layout: sidebar inputs, main outputs
    with st.sidebar:
        st.header("Patient Demographics")
        age = st.number_input("Age (years)", min_value=18, max_value=99, value=45, step=1)
        sex = st.selectbox("Sex", ["Male", "Female", "Other"])
        height_cm = st.number_input("Height (cm)", min_value=120.0, max_value=220.0, value=165.0, step=0.5)
        weight_kg = st.number_input("Weight (kg)", min_value=30.0, max_value=200.0, value=70.0, step=0.5)

        pregnancy_status = st.selectbox(
            "Pregnancy status",
            [
                "Non-pregnant",
                "Pregnant 1st trimester",
                "Pregnant 2nd trimester",
                "Pregnant 3rd trimester",
                "Postpartum <6 weeks",
            ],
        )

        st.header("Clinical Scenario")
        indication = st.radio(
            "Indication",
            ["Post-RAI for hyperthyroidism", "Post-thyroidectomy ± RAI for DTC"],
        )

        if indication == "Post-RAI for hyperthyroidism":
            st.subheader("Hyperthyroidism Context")
            hyper_dx = st.selectbox(
                "Underlying diagnosis",
                ["Graves’", "TMNG", "Toxic adenoma", "Other"],
            )
            time_since_RAI_months = st.number_input(
                "Time since RAI (months)", min_value=0.0, max_value=240.0, value=6.0, step=0.5
            )
        else:
            st.subheader("DTC Context")
            histology = st.selectbox(
                "Histology",
                ["Papillary", "Follicular", "Oncocytic", "Other DTC"],
            )
            initial_ata_risk = st.selectbox(
                "Initial ATA recurrence risk",
                ["Low", "Intermediate", "High"],
            )
            disease_status = st.selectbox(
                "Current response to therapy",
                ["Excellent", "Biochemical incomplete", "Structural incomplete", "Indeterminate"],
            )
            time_since_surgery_years = st.number_input(
                "Time since thyroidectomy (years)", min_value=0.0, max_value=50.0, value=2.0, step=0.5
            )
            received_RAI = st.checkbox("Patient has received RAI", value=True)

        st.header("Comorbidities / Risk Factors")
        ischemic_hd = st.checkbox("Ischemic heart disease")
        arrhythmia = st.checkbox("Atrial fibrillation / significant arrhythmia")
        heart_failure = st.checkbox("Heart failure")
        osteoporosis = st.checkbox("Known osteoporosis / high fracture risk")
        systemic_illness = st.checkbox("Severe systemic illness")

        st.header("Current Thyroid Labs & Therapy")
        current_tsh = st.number_input("Current TSH (mIU/L)", min_value=0.0, max_value=200.0, value=0.2, step=0.1)
        free_t4 = st.text_input("Free T4 (optional, with units)", value="")
        current_lt4 = st.number_input(
            "Current levothyroxine dose (mcg/day, 0 if not on LT4)",
            min_value=0.0,
            max_value=500.0,
            value=100.0,
            step=12.5,
        )
        current_lt4 = current_lt4 if current_lt4 > 0 else None

        other_thyroid_meds = st.text_input("Other thyroid-active meds (e.g., liothyronine, amiodarone)", value="")

        calculate = st.button("Calculate")

    # Main panel
    if calculate:
        inputs = {
            "age": age,
            "sex": sex,
            "height_cm": height_cm,
            "weight_kg": weight_kg,
            "pregnancy_status": pregnancy_status,
            "indication": indication,
            "current_tsh": current_tsh if current_tsh > 0 else None,
            "current_lt4": current_lt4,
            "ischemic_hd": ischemic_hd,
            "arrhythmia": arrhythmia,
            "heart_failure": heart_failure,
            "osteoporosis": osteoporosis,
        }

        if indication == "Post-thyroidectomy ± RAI for DTC":
            inputs.update(
                {
                    "initial_ata_risk": initial_ata_risk,
                    "disease_status": disease_status,
                    "time_since_surgery_years": time_since_surgery_years,
                }
            )
        else:
            inputs.update(
                {
                    "initial_ata_risk": None,
                    "disease_status": None,
                    "time_since_surgery_years": None,
                }
            )

        results = calculate_lt4_and_targets(inputs)

        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric(
                "Scenario",
                "Post-RAI hypothyroidism (non-DTC)" if results["scenario"] == "A" else "DTC post-thyroidectomy ± RAI",
            )
        with col2:
            st.metric("Suppression level", results["suppression_level"])
        with col3:
            tsh_low, tsh_high = results["tsh_target_range"]
            if tsh_low == 0.0 and tsh_high == 0.1:
                st.metric("TSH target", "< 0.1 mIU/L")
            else:
                st.metric("TSH target", f"{tsh_low:.2f} – {tsh_high:.2f} mIU/L")

        st.subheader("Levothyroxine dose recommendation")

        mcg_min, mcg_max = results["lt4_recommended_mcg_day_range"]
        mcg_kg_min, mcg_kg_max = results["lt4_recommended_mcg_kg_day_range"]
        central = results["lt4_suggested_central_mcg_day"]
        effective_weight = results["effective_weight_kg"]

        st.markdown(
            f"""
**Suggested LT4 dose range (mcg/day):** `{mcg_min:.0f} – {mcg_max:.0f}`  
**Central estimate:** `{central:.0f} mcg/day`  

Effective dosing weight used: `{effective_weight:.1f} kg`  
Corresponding range ≈ `{mcg_kg_min:.2f} – {mcg_kg_max:.2f} mcg/kg/day`
"""
        )

        if results["dose_adjustment_suggestion"]:
            st.info(results["dose_adjustment_suggestion"])

        st.subheader("Follow-up & Notes")
        st.write(
            f"**Suggested TSH re-check interval:** about **{results['followup_TSH_interval_weeks']} weeks** after any dose change."
        )
        st.write(results["special_note"])

        if results["safety_flags"]:
            st.subheader("Safety flags (review carefully)")
            for flag in results["safety_flags"]:
                st.error(flag)

        st.caption(
            "This calculator is an adjunct to, not a replacement for, clinical judgment, "
            "multidisciplinary discussion, and institutional protocols."
        )


if __name__ == "__main__":
    main()
