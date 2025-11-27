import math
import streamlit as st

# ==========================================
# 1. INDIAN MARKET UTILITIES
# ==========================================

def get_nearest_indian_tablet(target_dose: float) -> str:
    """
    Maps a calculated dose to available Indian market SKUs.
    Common Brands: Thyronorm, Eltroxin, Lethyrox.
    """
    # Standard strengths available in India
    indian_skus = [12.5, 25, 50, 62.5, 75, 88, 100, 112, 125, 137, 150, 175, 200]
    
    # Find exact or closest match
    closest_sku = min(indian_skus, key=lambda x: abs(x - target_dose))
    diff = abs(closest_sku - target_dose)

    if diff <= 5.0:
        return f"Tablet {closest_sku} mcg OD"
    else:
        # Handling awkward doses (e.g., 90mcg)
        lower = max([x for x in indian_skus if x < target_dose] or [12.5])
        upper = min([x for x in indian_skus if x > target_dose] or [200])
        return (f"Target ~{target_dose:.0f} mcg (Between sizes)\n"
                f"Option A: Tab {closest_sku} mcg (Closest)\n"
                f"Option B: Alternate {lower}/{upper} mcg")

# ==========================================
# 2. CORE CLINICAL LOGIC (ATA 2025 UPDATE)
# ==========================================

def compute_bmi(weight_kg: float, height_cm: float | None) -> float | None:
    if not height_cm or height_cm <= 0: return None
    h_m = height_cm / 100.0
    return weight_kg / (h_m ** 2)

def compute_effective_weight(weight_kg: float, height_cm: float | None) -> float:
    """
    Adjusts weight for obesity to prevent overdosing.
    """
    bmi = compute_bmi(weight_kg, height_cm)
    if bmi is None or bmi < 30: 
        return weight_kg

    h_m = height_cm / 100.0
    ideal_weight = 25 * (h_m ** 2)
    adjusted_weight = ideal_weight + 0.4 * (weight_kg - ideal_weight)
    return max(ideal_weight, min(weight_kg, adjusted_weight))

def apply_smart_switch_protocol(
    indication: str, 
    current_lt4: float, 
    current_tsh: float, 
    calculated_weight_based_dose: float
) -> tuple[float, str]:
    """
    Smart Switch for Benign Hypothyroidism Initiation.
    """
    if indication != "Benign Hypothyroidism" or current_lt4 > 0:
        return calculated_weight_based_dose, "Standard Calculation"

    if current_tsh < 10:
        smart_dose = 25.0 if calculated_weight_based_dose < 75 else 50.0
        return smart_dose, "Smart Switch: Graded Start (TSH < 10)"
    elif current_tsh < 20:
        smart_dose = 50.0 if calculated_weight_based_dose < 100 else 75.0
        return smart_dose, "Smart Switch: Graded Start (TSH 10-20)"
    else:
        return calculated_weight_based_dose, "Full Weight-based Replacement (TSH > 20)"

def map_ata_risk_and_response(risk: str, response: str, years_since_surgery: float | None) -> str:
    """
    ATA 2025 UPDATE: Handles 4-Tier Risk Stratification.
    """
    risk = (risk or "").lower()
    response = (response or "").lower()
    yrs = years_since_surgery or 0.0

    # 1. LOW RISK (Unifocal, No ETE, No Aggressive Histology)
    if risk == "low":
        if response == "excellent": return "None"      # TSH 0.5 - 2.0
        elif response == "structural incomplete": return "Strong"
        else: return "Mild"                            # TSH 0.1 - 0.5
        
    # 2. LOW-INTERMEDIATE (NEW 2025 CATEGORY)
    # e.g., Minimal ETE, small node burden
    elif risk == "low-intermediate":
        if response == "excellent": return "Mild"      # De-escalate to 0.1-0.5 or even 0.5-2.0
        elif response in ["biochemical incomplete", "indeterminate"]: return "Moderate"
        elif response == "structural incomplete": return "Strong"
        else: return "Mild"
        
    # 3. INTERMEDIATE-HIGH (NEW 2025 CATEGORY)
    # e.g., Aggressive histology, extensive nodes
    elif risk == "intermediate-high":
        if response == "excellent": return "Mild"      # Can still relax if excellent response > 5 yrs
        elif response in ["biochemical incomplete", "indeterminate"]: return "Moderate"
        else: return "Strong"

    # 4. HIGH RISK (Gross ETE, Distant Mets)
    elif risk == "high":
        if response == "excellent": return "Mild" if yrs >= 5 else "Moderate"
        else: return "Strong"
        
    return "Mild"

def soften_suppression_level(level: str, high_cv_risk: bool, high_bone_risk: bool, pregnancy: bool) -> str:
    if pregnancy and level == "Strong":
        return "Moderate" 

    if not (high_cv_risk or high_bone_risk):
        return level

    order = ["None", "Mild", "Moderate", "Strong"]
    idx = order.index(level) if level in order else 1
    new_idx = max(0, idx - 1)
    return order[new_idx]

def get_tsh_targets(scenario: str, suppression_level: str, pregnancy_status: str) -> tuple[float, float]:
    # 1. PREGNANCY
    if pregnancy_status in ["Trimester 1", "Trimester 2", "Trimester 3"]:
        if pregnancy_status == "Trimester 1": return (0.1, 2.5)
        else: return (0.2, 3.0)
    if pregnancy_status == "Planning Pregnancy": return (0.5, 2.5)

    # 2. SCENARIOS
    if scenario == "C": return (0.4, 4.0) # Benign
    if scenario == "A": return (0.5, 2.5) # Post-RAI

    # 3. CANCER (ATA 2025 targets are similar, but applied to new groups)
    level = (suppression_level or "None").capitalize()
    if level == "None": return 0.5, 2.0
    elif level == "Mild": return 0.1, 0.5
    elif level == "Moderate": return 0.1, 0.5 # Strict end of mild
    elif level == "Strong": return 0.01, 0.1
    
    return 0.5, 4.0

def base_replacement_mcg_per_kg(age: int, high_cv_risk: bool, indication: str) -> float:
    base = 1.6 
    if indication == "Post-thyroidectomy for Ca (DTC)":
        base = 2.0 
    
    if high_cv_risk: return 1.0 
    elif age > 60: return 1.4
    else: return base

# ==========================================
# 3. TITRATION LOGIC
# ==========================================

def calculate_titration_step(current_lt4, ideal_lt4, current_tsh, t_high, t_low, high_cv_risk, pregnancy_status):
    is_pregnant = pregnancy_status in ["Trimester 1", "Trimester 2", "Trimester 3"]

    if current_lt4 <= 0:
        return ("Initiate therapy.", ideal_lt4)

    if is_pregnant:
        if current_tsh > t_high:
            safe_next = current_lt4 * 1.25
            return ("🚨 **Pregnant:** Increase dose by ~25-30% immediately.", safe_next)
    
    dose_gap = ideal_lt4 - current_lt4

    if current_tsh > t_high:
        if high_cv_risk:
            return (f"TSH High + CV Risk. Increase by max 12.5 mcg.", current_lt4 + 12.5)
        else:
            if dose_gap > 25:
                return (f"TSH High. Large gap. Increase by 25 mcg (Step 1).", current_lt4 + 25.0)
            elif dose_gap > 12.5:
                return (f"TSH High. Increase by 12.5 - 25 mcg.", current_lt4 + 12.5) 
            else:
                return (f"TSH High. Adjust to target.", ideal_lt4)

    elif current_tsh < t_low:
        if high_cv_risk:
             return (f"TSH Suppressed + High Risk. Reduce by 12.5 - 25 mcg immediately.", current_lt4 - 12.5)
        else:
             return (f"TSH Low. Reduce by 12.5 mcg.", current_lt4 - 12.5)
    
    return ("TSH on target. Continue current dose.", current_lt4)

def build_safety_flags(age, high_cv_risk, high_bone_risk, pregnancy_status, diabetes, suggested_mcg, effective_weight, current_tsh, suppression_level):
    flags = []
    if high_cv_risk: flags.append("⚠️ **High CV Risk:** Start low, go slow (12.5mcg steps).")
    if diabetes: flags.append("⚠️ **Diabetes:** Monitor for silent ischemia if increasing dose.")
    if pregnancy_status != "Non-pregnant": flags.append("🤰 **Pregnancy Protocol:** Check TSH every 4 weeks.")
    if effective_weight > 0 and (suggested_mcg / effective_weight) > 2.4:
        flags.append("⚠️ **High Dose Alert:** >2.4 mcg/kg. Check compliance.")
    return flags

def calculate_lt4_and_targets(inputs: dict) -> dict:
    age = inputs["age"]
    sex = inputs["sex"]
    weight_kg = inputs["weight_kg"]
    height_cm = inputs["height_cm"]
    indication = inputs["indication"]
    preg_status = inputs["pregnancy_status"]
    
    high_cv_risk = (age >= 60 or inputs.get("ischemic_hd") or inputs.get("arrhythmia") or inputs.get("heart_failure") or inputs.get("diabetes"))
    high_bone_risk = inputs.get("osteoporosis") or (sex == "Female" and age >= 55)

    if indication == "Post-RAI for hyperthyroidism": scenario = "A"
    elif indication == "Post-thyroidectomy for Ca (DTC)": scenario = "B"
    else: scenario = "C"

    suppression_level = "None"
    if scenario == "B":
        suppression_level = map_ata_risk_and_response(inputs.get("initial_ata_risk"), inputs.get("disease_status"), inputs.get("time_since_surgery_years"))
        suppression_level = soften_suppression_level(suppression_level, high_cv_risk, high_bone_risk, preg_status != "Non-pregnant")

    tsh_low, tsh_high = get_tsh_targets(scenario, suppression_level, preg_status)
    effective_weight = compute_effective_weight(weight_kg, height_cm)
    base_mcg = base_replacement_mcg_per_kg(age, high_cv_risk, indication)
    
    factor = 1.0
    if scenario == "B":
        if suppression_level == "Mild": factor = 1.1
        elif suppression_level == "Moderate": factor = 1.2
        elif suppression_level == "Strong": factor = 1.3
    
    theoretical_dose = base_mcg * effective_weight * factor
    current_lt4 = inputs.get("current_lt4", 0)
    current_tsh = inputs.get("current_tsh", 5.0)
    
    ideal_dose, calculation_method_note = apply_smart_switch_protocol(indication, current_lt4, current_tsh, theoretical_dose)

    if preg_status in ["Trimester 1", "Trimester 2", "Trimester 3"]:
        if current_lt4 > 0: ideal_dose = current_lt4 * 1.25

    if ideal_dose > 300: ideal_dose = 300

    titration_text, safe_next_dose = calculate_titration_step(current_lt4, ideal_dose, current_tsh, tsh_high, tsh_low, high_cv_risk, preg_status)
    safety_flags = build_safety_flags(age, high_cv_risk, high_bone_risk, preg_status, inputs.get("diabetes"), ideal_dose, effective_weight, current_tsh, suppression_level)

    return {
        "scenario": scenario,
        "suppression_level": suppression_level,
        "tsh_target_range": (tsh_low, tsh_high),
        "ideal_calculated_dose": ideal_dose,
        "calculation_note": calculation_method_note,
        "safe_next_dose": safe_next_dose,
        "titration_note": titration_text,
        "safety_flags": safety_flags,
        "effective_weight": effective_weight
    }

# ==========================================
# 4. STREAMLIT UI
# ==========================================

def main():
    st.set_page_config(page_title="Thyroid Calc v4.0 (ATA 2025)", layout="wide")
    st.title("🇮🇳 Thyroid CDSS: ATA 2025 & Indian Standards")
    st.markdown("**Protocols: ATA 2025 (4-Tier Risk) | Pregnancy Y-Rule | Smart Switch**")

    with st.sidebar:
        st.header("1. Demographics")
        age = st.number_input("Age", 18, 99, 30)
        sex = st.selectbox("Sex", ["Female", "Male"])
        
        preg_options = ["Non-pregnant"]
        if sex == "Female" and age < 55:
            preg_options = ["Non-pregnant", "Planning Pregnancy", "Trimester 1", "Trimester 2", "Trimester 3"]
        pregnancy_status = st.selectbox("Pregnancy Status", preg_options)
        
        weight_kg = st.number_input("Weight (kg)", 30.0, 150.0, 60.0, step=0.5)
        height_cm = st.number_input("Height (cm)", 120.0, 210.0, 160.0, step=1.0)
        
        st.header("2. Indication")
        indication = st.radio("Diagnosis", ["Benign Hypothyroidism", "Post-thyroidectomy for Ca (DTC)", "Post-RAI for hyperthyroidism"])
        
        inputs = {"age": age, "sex": sex, "weight_kg": weight_kg, "height_cm": height_cm, "indication": indication, "pregnancy_status": pregnancy_status}

        if indication == "Post-thyroidectomy for Ca (DTC)":
            st.subheader("ATA 2025 Risk Stratification")
            # UPDATED DROPDOWN FOR 2025
            inputs["initial_ata_risk"] = st.selectbox(
                "Initial ATA Risk", 
                ["Low", "Low-Intermediate", "Intermediate-High", "High"],
                help="2025 Guidelines split Intermediate into Low-Int and Int-High"
            )
            inputs["disease_status"] = st.selectbox("Response to Therapy", ["Excellent", "Indeterminate", "Biochemical Incomplete", "Structural Incomplete"])
            inputs["time_since_surgery_years"] = st.number_input("Years since surgery", 0.0, 30.0, 1.0)

        st.header("3. Comorbidities")
        c1, c2 = st.columns(2)
        with c1:
            inputs["diabetes"] = st.checkbox("Diabetes")
            inputs["ischemic_hd"] = st.checkbox("IHD")
        with c2:
            inputs["arrhythmia"] = st.checkbox("Arrhythmia")
            inputs["osteoporosis"] = st.checkbox("Osteoporosis")

        st.header("4. Current Status")
        inputs["current_lt4"] = st.number_input("Current Dose (mcg) - 0 if Naive", 0.0, 300.0, 50.0, step=12.5)
        inputs["current_tsh"] = st.number_input("Current TSH (mIU/L)", 0.0, 150.0, 8.5, step=0.1)

        btn_calc = st.button("Calculate Prescription", type="primary")

    if btn_calc:
        res = calculate_lt4_and_targets(inputs)
        
        st.divider()
        col_main_1, col_main_2 = st.columns([1, 1.2])

        with col_main_1:
            st.subheader("🎯 TSH Targets")
            t_low, t_high = res['tsh_target_range']
            st.metric("Target TSH Range", f"{t_low} - {t_high} mIU/L")
            
            if "Smart Switch" in res['calculation_note']:
                st.info(f"🧠 **Protocol:** {res['calculation_note']}")
            elif pregnancy_status != "Non-pregnant":
                 st.info(f"🤰 **Pregnancy:** Range adjusted for {pregnancy_status}.")
            elif res['scenario'] == "B":
                 st.info(f"🎗️ **Oncology (ATA 2025):** {res['suppression_level']} Suppression")

            if res['safety_flags']:
                st.warning("⚠️ **Safety Alerts:**")
                for flag in res['safety_flags']:
                    st.write(f"- {flag}")

        with col_main_2:
            st.subheader("💊 Prescription Guide")
            
            safe_dose_sku = get_nearest_indian_tablet(res['safe_next_dose'])
            
            st.success(f"**Prescribe Today:** {safe_dose_sku}")
            
            if abs(res['safe_next_dose'] - res['ideal_calculated_dose']) > 10:
                st.info(f"🏁 **Long Term Goal:** ~{res['ideal_calculated_dose']:.0f} mcg\n\n(Re-titrate after 6-8 weeks)")
            else:
                st.caption(f"Based on: {res['calculation_note']}")

            st.caption("Brands: Thyronorm, Eltroxin, Lethyrox")
            
            st.markdown("---")
            st.markdown(f"**📈 Clinical Reasoning:**")
            st.write(res['titration_note'])

if __name__ == "__main__":
    main()
