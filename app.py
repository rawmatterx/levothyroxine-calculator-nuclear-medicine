import math
import streamlit as st

# ==========================================
# 1. INDIAN MARKET UTILITIES & DATA
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
# 2. CORE CLINICAL LOGIC
# ==========================================

def compute_bmi(weight_kg: float, height_cm: float | None) -> float | None:
    if not height_cm or height_cm <= 0: return None
    h_m = height_cm / 100.0
    return weight_kg / (h_m ** 2)

def compute_effective_weight(weight_kg: float, height_cm: float | None) -> float:
    """
    Adjusts weight for obesity to prevent overdosing in high BMI patients.
    """
    bmi = compute_bmi(weight_kg, height_cm)
    if bmi is None or bmi < 30: 
        return weight_kg

    h_m = height_cm / 100.0
    ideal_weight = 25 * (h_m ** 2)
    # Adjusted weight formula: Ideal + 40% of excess weight
    adjusted_weight = ideal_weight + 0.4 * (weight_kg - ideal_weight)
    return max(ideal_weight, min(weight_kg, adjusted_weight))

def map_ata_risk_and_response(risk: str, response: str, years_since_surgery: float | None) -> str:
    """
    Determines suppression intensity (None/Mild/Mod/Strong) based on DTC Risk + Response.
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
        if response == "excellent": return "Mild" if yrs >= 5 else "Moderate"
        elif response == "structural incomplete": return "Strong"
        else: return "Strong"
        
    return "Mild"

def soften_suppression_level(level: str, high_cv_risk: bool, high_bone_risk: bool, pregnancy: bool) -> str:
    """
    Downgrades suppression targets if patient has comorbidities or is pregnant.
    """
    # Pregnancy safety override
    if pregnancy and level == "Strong":
        return "Moderate" 

    if not (high_cv_risk or high_bone_risk):
        return level

    order = ["None", "Mild", "Moderate", "Strong"]
    idx = order.index(level) if level in order else 1
    new_idx = max(0, idx - 1)
    return order[new_idx]

def get_tsh_targets(scenario: str, suppression_level: str, pregnancy_status: str) -> tuple[float, float]:
    # 1. PREGNANCY LOGIC (Overrides others)
    if pregnancy_status in ["Trimester 1", "Trimester 2", "Trimester 3"]:
        if pregnancy_status == "Trimester 1":
            return (0.1, 2.5)
        else:
            return (0.2, 3.0)

    # 2. PLANNING PREGNANCY
    if pregnancy_status == "Planning Pregnancy":
        return (0.5, 2.5)

    # 3. STANDARD SCENARIOS
    if scenario == "C": # Benign Hypo
        return (0.4, 4.0)
    
    if scenario == "A": # Post-RAI (Hyper)
        return (0.5, 2.5)

    # Scenario B: Cancer
    level = (suppression_level or "None").capitalize()
    if level == "None": return 0.5, 2.0
    elif level == "Mild": return 0.1, 0.5
    elif level == "Moderate": return 0.1, 0.5
    elif level == "Strong": return 0.01, 0.1
    
    return 0.5, 4.0

def base_replacement_mcg_per_kg(age: int, high_cv_risk: bool, indication: str) -> float:
    base = 1.6 
    if indication == "Post-thyroidectomy for Ca (DTC)":
        base = 2.0 # Suppression needs higher dose
    
    if high_cv_risk:
        return 1.0 # Start Low
    elif age > 60:
        return 1.4 # Elderly
    else:
        return base

# ==========================================
# 3. TITRATION LOGIC (THE SAFETY FIX)
# ==========================================

def calculate_titration_step(
    current_lt4: float, 
    ideal_lt4: float, 
    current_tsh: float, 
    t_high: float, 
    t_low: float, 
    high_cv_risk: bool,
    pregnancy_status: str
) -> tuple[str, float]:
    """
    Returns a TUPLE: (Advice Text, Safe Next Dose)
    This prevents the app from suggesting massive jumps (e.g. 50->112) in one go.
    """
    is_pregnant = pregnancy_status in ["Trimester 1", "Trimester 2", "Trimester 3"]

    # CASE 0: Naive Patient (Not on meds)
    if current_lt4 <= 0:
        if high_cv_risk and not is_pregnant:
            return ("Initiate conservatively due to CV risk.", 25.0)
        return ("Initiate full calculated replacement.", ideal_lt4)

    # CASE 1: Pregnancy (Urgent & Aggressive)
    if is_pregnant:
        if current_tsh > t_high:
            # ATA Rule: Immediate ~25-30% increase
            safe_next = current_lt4 * 1.25
            # Round to nearest logical step (e.g., +25mcg)
            return ("🚨 **Pregnant:** Increase dose by ~25-30% immediately.", safe_next)
    
    dose_gap = ideal_lt4 - current_lt4

    # CASE 2: TSH HIGH (Under-treated)
    if current_tsh > t_high:
        if high_cv_risk:
            # SAFETY LIMIT: Max increase 12.5 mcg
            return (f"TSH High + CV Risk. Increase by max 12.5 mcg.", current_lt4 + 12.5)
        else:
            # No CV Risk
            if dose_gap > 25:
                # Large gap (e.g., 50 -> 112). Don't jump. Step +25.
                return (f"TSH High. Large gap to target. Increase by 25 mcg (Step 1).", current_lt4 + 25.0)
            elif dose_gap > 12.5:
                return (f"TSH High. Increase by 12.5 - 25 mcg.", current_lt4 + 12.5) # Conservative step
            else:
                return (f"TSH High. Adjust to target.", ideal_lt4)

    # CASE 3: TSH LOW (Over-treated)
    elif current_tsh < t_low:
        if high_cv_risk:
             return (f"TSH Suppressed + High Risk. Reduce by 12.5 - 25 mcg immediately.", current_lt4 - 12.5)
        else:
             return (f"TSH Low. Reduce by 12.5 mcg.", current_lt4 - 12.5)
    
    # CASE 4: On Target
    return ("TSH on target. Continue current dose.", current_lt4)


def build_safety_flags(age, high_cv_risk, high_bone_risk, pregnancy_status, diabetes, suggested_mcg, effective_weight, current_tsh, suppression_level):
    flags = []
    if high_cv_risk: flags.append("⚠️ **High CV Risk:** Start low, go slow (12.5mcg steps).")
    if diabetes: flags.append("⚠️ **Diabetes:** Monitor for silent ischemia if increasing dose.")
    if pregnancy_status != "Non-pregnant": flags.append("🤰 **Pregnancy Protocol:** Check TSH every 4 weeks.")
    
    if effective_weight > 0:
        if (suggested_mcg / effective_weight) > 2.4:
            flags.append("⚠️ **High Dose Alert:** >2.4 mcg/kg. Check compliance/malabsorption.")
    return flags

def calculate_lt4_and_targets(inputs: dict) -> dict:
    # Unpack inputs
    age = inputs["age"]
    sex = inputs["sex"]
    weight_kg = inputs["weight_kg"]
    height_cm = inputs["height_cm"]
    indication = inputs["indication"]
    preg_status = inputs["pregnancy_status"]
    
    # Risk factors
    high_cv_risk = (age >= 60 or inputs.get("ischemic_hd") or inputs.get("arrhythmia") or inputs.get("heart_failure") or inputs.get("diabetes"))
    high_bone_risk = inputs.get("osteoporosis") or (sex == "Female" and age >= 55)

    # Scenario Logic
    if indication == "Post-RAI for hyperthyroidism": scenario = "A"
    elif indication == "Post-thyroidectomy for Ca (DTC)": scenario = "B"
    else: scenario = "C" # Benign

    # Suppression
    suppression_level = "None"
    if scenario == "B":
        suppression_level = map_ata_risk_and_response(inputs.get("initial_ata_risk"), inputs.get("disease_status"), inputs.get("time_since_surgery_years"))
        suppression_level = soften_suppression_level(suppression_level, high_cv_risk, high_bone_risk, preg_status != "Non-pregnant")

    # Targets & Dosing
    tsh_low, tsh_high = get_tsh_targets(scenario, suppression_level, preg_status)
    effective_weight = compute_effective_weight(weight_kg, height_cm)
    base_mcg = base_replacement_mcg_per_kg(age, high_cv_risk, indication)
    
    # Suppression Multiplier
    factor = 1.0
    if scenario == "B":
        if suppression_level == "Mild": factor = 1.1
        elif suppression_level == "Moderate": factor = 1.2
        elif suppression_level == "Strong": factor = 1.3
    
    ideal_dose = base_mcg * effective_weight * factor
    
    # Pregnancy Boost (The Y-Rule)
    if preg_status in ["Trimester 1", "Trimester 2", "Trimester 3"]:
        current = inputs.get("current_lt4", 0)
        # If already on meds, increase by 25%. If naive, calc is already done above.
        if current > 0: ideal_dose = current * 1.25

    if ideal_dose > 300: ideal_dose = 300

    # Titration & Safety
    titration_text, safe_next_dose = calculate_titration_step(
        inputs.get("current_lt4", 0), ideal_dose, inputs.get("current_tsh", 0), 
        tsh_high, tsh_low, high_cv_risk, preg_status
    )

    safety_flags = build_safety_flags(age, high_cv_risk, high_bone_risk, preg_status, inputs.get("diabetes"), ideal_dose, effective_weight, inputs.get("current_tsh"), suppression_level)

    return {
        "scenario": scenario,
        "suppression_level": suppression_level,
        "tsh_target_range": (tsh_low, tsh_high),
        "ideal_calculated_dose": ideal_dose,
        "safe_next_dose": safe_next_dose,      # The Safe Step
        "titration_note": titration_text,
        "safety_flags": safety_flags,
        "effective_weight": effective_weight
    }

# ==========================================
# 4. STREAMLIT UI
# ==========================================

def main():
    st.set_page_config(page_title="Thyroid Calc v2.1 (Safe Titration)", layout="wide")
    st.title("🇮🇳 Thyroid CDSS: Pregnancy & Oncology")
    st.markdown("**Indian Standard of Care | ATA 2024/2025 Guidelines**")

    # --- SIDEBAR ---
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
            st.subheader("DTC Risk")
            inputs["initial_ata_risk"] = st.selectbox("Initial ATA Risk", ["Low", "Intermediate", "High"])
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
        inputs["current_lt4"] = st.number_input("Current Dose (mcg)", 0.0, 300.0, 50.0, step=12.5)
        inputs["current_tsh"] = st.number_input("Current TSH (mIU/L)", 0.0, 150.0, 4.5, step=0.1)

        btn_calc = st.button("Calculate Prescription", type="primary")

    # --- MAIN DISPLAY ---
    if btn_calc:
        res = calculate_lt4_and_targets(inputs)
        
        st.divider()
        col_main_1, col_main_2 = st.columns([1, 1.2])

        with col_main_1:
            st.subheader("🎯 TSH Targets")
            t_low, t_high = res['tsh_target_range']
            st.metric("Target TSH Range", f"{t_low} - {t_high} mIU/L")
            
            if pregnancy_status != "Non-pregnant":
                 st.info(f"**Pregnancy:** Range adjusted for {pregnancy_status}.")
            elif res['scenario'] == "B":
                 st.info(f"**Oncology:** {res['suppression_level']} Suppression")

            if res['safety_flags']:
                st.warning("⚠️ **Safety Alerts:**")
                for flag in res['safety_flags']:
                    st.write(f"- {flag}")

        with col_main_2:
            st.subheader("💊 Prescription Guide")
            
            # --- SAFETY FIX DISPLAY LOGIC ---
            # 1. We display the SAFE NEXT STEP in the Green Box
            safe_dose_sku = get_nearest_indian_tablet(res['safe_next_dose'])
            
            st.success(f"**Prescribe Today:** {safe_dose_sku}")
            
            # 2. We display the Long Term Goal separately if it differs
            if abs(res['safe_next_dose'] - res['ideal_calculated_dose']) > 10:
                st.info(f"🏁 **Long Term Goal:** ~{res['ideal_calculated_dose']:.0f} mcg\n\n(Re-titrate after 6-8 weeks)")
            else:
                st.caption(f"Calculated Ideal Body Weight Dose: {res['ideal_calculated_dose']:.0f} mcg")

            st.caption("Brands: Thyronorm, Eltroxin, Lethyrox")
            
            st.markdown("---")
            st.markdown(f"**📈 Clinical Reasoning:**")
            st.write(res['titration_note'])

if __name__ == "__main__":
    main()
