import math
import streamlit as st

# ==========================
# 1. INDIAN MARKET UTILITIES
# ==========================

def get_nearest_indian_tablet(target_dose: float) -> str:
    """
    Maps a calculated theoretical dose to available Indian market SKUs 
    (commonly Abbott Thyronorm, GSK Eltroxin, Berlin-Chemie Lethyrox).
    """
    # Standard strengths available in India
    indian_skus = [12.5, 25, 50, 62.5, 75, 88, 100, 112, 125, 137, 150, 175, 200]
    
    # Find exact or closest match
    closest_sku = min(indian_skus, key=lambda x: abs(x - target_dose))
    diff = abs(closest_sku - target_dose)

    if diff <= 5.0:
        return f"Tablet {closest_sku} mcg OD"
    else:
        # If the target falls awkwardly between sizes (e.g. 90mcg)
        # Suggest splitting logic or alternate days which is common in India
        lower = max([x for x in indian_skus if x < target_dose] or [12.5])
        upper = min([x for x in indian_skus if x > target_dose] or [200])
        
        return (f"Target ~{target_dose:.0f} mcg is between standard sizes.\n"
                f"Option A: Tab {closest_sku} mcg (closest)\n"
                f"Option B: Alternate {lower} mcg and {upper} mcg")

# ==========================
# 2. CORE CLINICAL LOGIC
# ==========================

def compute_bmi(weight_kg: float, height_cm: float | None) -> float | None:
    if not height_cm or height_cm <= 0:
        return None
    h_m = height_cm / 100.0
    return weight_kg / (h_m ** 2)

def compute_effective_weight(weight_kg: float, height_cm: float | None) -> float:
    """
    Adjusts weight for obesity to prevent overdosing.
    """
    bmi = compute_bmi(weight_kg, height_cm)
    if bmi is None or bmi < 30: # Stricter cutoff for Asian obesity phenotype? Kept 30 for safety
        return weight_kg

    h_m = height_cm / 100.0
    ideal_weight = 25 * (h_m ** 2)
    # Adjusted weight formula
    adjusted_weight = ideal_weight + 0.4 * (weight_kg - ideal_weight)
    return max(ideal_weight, min(weight_kg, adjusted_weight))

def map_ata_risk_and_response(risk: str, response: str, years_since_surgery: float | None) -> str:
    """
    Determines suppression intensity: None, Mild, Moderate, Strong
    """
    risk = (risk or "").lower()
    response = (response or "").lower()
    yrs = years_since_surgery or 0.0

    if risk == "low":
        if response == "excellent": return "None"
        elif response == "structural incomplete": return "Strong"
        else: return "Mild"
        
    elif risk == "intermediate":
        if response == "excellent": return "Mild"
        elif response in ["biochemical incomplete", "indeterminate"]: return "Moderate"
        elif response == "structural incomplete": return "Strong"
        else: return "Moderate"
        
    elif risk == "high":
        if response == "excellent":
            return "Mild" if yrs >= 5 else "Moderate"
        elif response == "structural incomplete": return "Strong"
        else: return "Strong"
        
    return "Mild"

def soften_suppression_level(level: str, high_cv_risk: bool, high_bone_risk: bool) -> str:
    """
    Downgrades suppression targets if patient has comorbidities.
    """
    if not (high_cv_risk or high_bone_risk):
        return level

    order = ["None", "Mild", "Moderate", "Strong"]
    idx = order.index(level) if level in order else 1
    new_idx = max(0, idx - 1)
    return order[new_idx]

def suppression_to_tsh_range(level: str, scenario: str) -> tuple[float, float]:
    level = (level or "None").capitalize()
    if scenario == "A": return 0.5, 2.5 # Benign / Post-RAI Graves
    
    # Cancer Scenarios
    if level == "None": return 0.5, 2.0
    elif level == "Mild": return 0.1, 0.5
    elif level == "Moderate": return 0.1, 0.5 # Often overlaps with Mild in practice, but stricter adherence
    elif level == "Strong": return 0.01, 0.1
    return 0.5, 2.0

def suppression_level_to_factor(level: str) -> float:
    level = (level or "None").capitalize()
    if level == "None": return 1.0
    elif level == "Mild": return 1.10
    elif level == "Moderate": return 1.20
    elif level == "Strong": return 1.30
    return 1.0

def base_replacement_mcg_per_kg(age: int, high_cv_risk: bool) -> float:
    if high_cv_risk:
        return 1.0 # Cautionary start
    elif age > 60:
        return 1.4 # Elderly but healthy
    else:
        return 1.6 # Young/Healthy replacement

def calculate_titration_suggestion(
    current_lt4: float, 
    ideal_lt4: float, 
    current_tsh: float, 
    target_tsh_high: float, 
    target_tsh_low: float,
    high_cv_risk: bool
) -> str:
    """
    Smart Logic: Suggests increment/decrement based on the GAP between current and ideal.
    """
    if current_lt4 <= 0:
        return "Initiate therapy at calculated dose."

    dose_gap = ideal_lt4 - current_lt4

    # CASE 1: TSH is High (Under-treated)
    if current_tsh > target_tsh_high:
        if high_cv_risk:
            return f"TSH High. Safety protocol: Increase by max 12.5 mcg (Total: {current_lt4 + 12.5} mcg). Recheck 6-8 wks."
        else:
            # If gap is large (>20mcg), can be aggressive
            if dose_gap > 20:
                return f"TSH High & Dose Low. Consider increasing by 25 mcg (Total: {current_lt4 + 25} mcg)."
            elif dose_gap > 10:
                return f"TSH High. Increase by 12.5 mcg (Total: {current_lt4 + 12.5} mcg)."
            else:
                return "TSH High but current dose matches weight-based calc. Check compliance/absorption before increasing."

    # CASE 2: TSH is Low (Over-treated)
    elif current_tsh < target_tsh_low:
        if high_cv_risk:
             return f"TSH Suppressed + High Risk. Reduce by 12.5 - 25 mcg immediately."
        else:
             return f"TSH Low. Consider reducing by 12.5 mcg (Target: {current_lt4 - 12.5} mcg)."
    
    return "TSH on target. Continue current dose."

def build_safety_flags(
    age: int,
    high_cv_risk: bool,
    high_bone_risk: bool,
    pregnancy_status: str,
    diabetes: bool,
    suggested_mcg_day: float,
    effective_weight_kg: float,
    current_tsh: float | None,
    suppression_level: str
) -> list[str]:
    flags = []

    if high_cv_risk:
        flags.append("⚠️ **High CV Risk Protocol:** Start low, titrate slow (12.5mcg steps). Avoid TSH < 0.1.")
    
    if diabetes:
        flags.append("⚠️ **Diabetes:** Monitor for silent ischemia if initiating high-dose suppression.")

    if high_bone_risk and suppression_level in ["Moderate", "Strong"]:
        flags.append("⚠️ **Bone Health:** Prolonged suppression increases fracture risk. Ensure Ca/VitD supplementation.")

    if pregnancy_status != "Non-pregnant":
        flags.append("🤰 **Pregnancy:** Dose requirements usually increase 20-30%. Consult OB-Endo guidelines.")

    if effective_weight_kg > 0:
        mcg_per_kg = suggested_mcg_day / effective_weight_kg
        if mcg_per_kg > 2.2:
            flags.append(f"⚠️ **High Dose Warning:** Calculated dose >2.2 mcg/kg. Rule out malabsorption (H. Pylori, Gastritis) or poor compliance.")

    return flags

def calculate_lt4_and_targets(inputs: dict) -> dict:
    age = inputs["age"]
    sex = inputs["sex"]
    height_cm = inputs.get("height_cm")
    weight_kg = inputs["weight_kg"]
    indication = inputs["indication"]
    
    # Comorbidities
    ischemic_hd = inputs.get("ischemic_hd", False)
    arrhythmia = inputs.get("arrhythmia", False)
    heart_failure = inputs.get("heart_failure", False)
    diabetes = inputs.get("diabetes", False)
    osteoporosis = inputs.get("osteoporosis", False)
    
    # Logic Update: Indian Phenotype CV Risk
    # Lowered age threshold (60) + Added Diabetes as risk factor
    high_cv_risk = (
        age >= 60 
        or ischemic_hd 
        or arrhythmia 
        or heart_failure 
        or diabetes
    )
    
    high_bone_risk = osteoporosis or (sex == "Female" and age >= 55)

    # Scenarios
    scenario = "A" if indication == "Post-RAI for hyperthyroidism" else "B"
    
    if scenario == "A":
        suppression_level = "None"
    else:
        suppression_level = map_ata_risk_and_response(
            inputs.get("initial_ata_risk"), 
            inputs.get("disease_status"), 
            inputs.get("time_since_surgery_years")
        )
        suppression_level = soften_suppression_level(suppression_level, high_cv_risk, high_bone_risk)

    tsh_low, tsh_high = suppression_to_tsh_range(suppression_level, scenario)

    # Dosing Math
    effective_weight = compute_effective_weight(weight_kg, height_cm)
    base_mcg_per_kg = base_replacement_mcg_per_kg(age, high_cv_risk)
    
    # Apply suppression multiplier
    factor = suppression_level_to_factor(suppression_level if scenario == "B" else "None")
    
    suggested_central_mcg_day = base_mcg_per_kg * effective_weight * factor

    # Cap for safety
    if suggested_central_mcg_day > 250: suggested_central_mcg_day = 250
    
    # Titration Logic
    current_tsh = inputs.get("current_tsh")
    current_lt4 = inputs.get("current_lt4")
    
    titration_note = None
    if current_tsh is not None and current_lt4 is not None:
        titration_note = calculate_titration_suggestion(
            current_lt4, suggested_central_mcg_day, current_tsh, tsh_high, tsh_low, high_cv_risk
        )

    safety_flags = build_safety_flags(
        age, high_cv_risk, high_bone_risk, inputs["pregnancy_status"], diabetes,
        suggested_central_mcg_day, effective_weight, current_tsh, suppression_level
    )

    return {
        "scenario": scenario,
        "suppression_level": suppression_level,
        "tsh_target_range": (tsh_low, tsh_high),
        "ideal_calculated_dose": suggested_central_mcg_day,
        "titration_note": titration_note,
        "safety_flags": safety_flags,
        "effective_weight": effective_weight
    }

# ==========================
# 3. STREAMLIT UI
# ==========================

def main():
    st.set_page_config(page_title="NM Thyroid Calculator (India)", layout="wide")
    
    st.title("🇮🇳 Thyroid Dose & Suppression Calculator")
    st.markdown("**For Indian Nuclear Medicine / Endocrinology Practice** | *NMC/MCI Compliance: Decision Support Only*")

    # --- SIDEBAR INPUTS ---
    with st.sidebar:
        st.header("1. Demographics")
        age = st.number_input("Age", 18, 99, 45)
        sex = st.selectbox("Sex", ["Female", "Male"])
        weight_kg = st.number_input("Weight (kg)", 30.0, 150.0, 65.0, step=0.5)
        height_cm = st.number_input("Height (cm)", 120.0, 210.0, 160.0, step=1.0)
        
        st.header("2. Clinical Context")
        indication = st.radio("Diagnosis", ["Post-thyroidectomy for Ca (DTC)", "Post-RAI for hyperthyroidism"])
        
        inputs = {
            "age": age, "sex": sex, "weight_kg": weight_kg, "height_cm": height_cm, 
            "indication": indication, "pregnancy_status": "Non-pregnant" # Simplified for this view
        }

        if indication == "Post-thyroidectomy for Ca (DTC)":
            st.subheader("Risk Stratification")
            inputs["initial_ata_risk"] = st.selectbox("Initial ATA Risk", ["Low", "Intermediate", "High"])
            inputs["disease_status"] = st.selectbox("Response to Therapy", ["Excellent", "Indeterminate", "Biochemical Incomplete", "Structural Incomplete"])
            inputs["time_since_surgery_years"] = st.number_input("Years since surgery", 0.0, 30.0, 1.0)

        st.header("3. Comorbidities (Indian Context)")
        c1, c2 = st.columns(2)
        with c1:
            inputs["diabetes"] = st.checkbox("Diabetes (Long term)")
            inputs["ischemic_hd"] = st.checkbox("Ischemic Heart Disease")
        with c2:
            inputs["arrhythmia"] = st.checkbox("Arrhythmia")
            inputs["osteoporosis"] = st.checkbox("Osteoporosis")

        st.header("4. Current Status")
        inputs["current_lt4"] = st.number_input("Current Dose (mcg)", 0.0, 300.0, 100.0, step=12.5)
        inputs["current_tsh"] = st.number_input("Current TSH (mIU/L)", 0.0, 150.0, 2.5, step=0.1)

        btn_calc = st.button("Calculate Prescription", type="primary")

    # --- MAIN OUTPUT ---
    if btn_calc:
        res = calculate_lt4_and_targets(inputs)
        
        # Display Logic
        st.divider()
        col_main_1, col_main_2 = st.columns([1, 1.2])

        with col_main_1:
            st.subheader("🎯 TSH Targets")
            t_low, t_high = res['tsh_target_range']
            
            # Suppression Visual
            st.metric("Target TSH Range", f"{t_low} - {t_high} mIU/L", help="Based on dynamic risk stratification")
            st.info(f"**Clinical Goal:** {res['suppression_level']} Suppression")

            if res['safety_flags']:
                st.warning("⚠️ **Safety Alerts:**")
                for flag in res['safety_flags']:
                    st.write(f"- {flag}")

        with col_main_2:
            st.subheader("💊 Prescription Guide")
            
            # 1. Theoretical Calculation
            calc_dose = res['ideal_calculated_dose']
            
            # 2. Indian Market Tablet Logic
            market_tablet = get_nearest_indian_tablet(calc_dose)
            
            st.markdown(f"""
            **Calculated Ideal Dose:** `{calc_dose:.1f} mcg/day`  
            *(Based on Effective Weight: {res['effective_weight']:.1f} kg)*
            """)
            
            st.success(f"**Recommended SKU:** {market_tablet}")
            st.caption("Brands: Thyronorm, Eltroxin, Lethyrox, Roxithro")
            
            # 3. Titration Advice
            if res['titration_note']:
                st.markdown("---")
                st.markdown(f"**📈 Titration Advice:**")
                st.write(res['titration_note'])

if __name__ == "__main__":
    main()
