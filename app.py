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
    if bmi is None or bmi < 30: 
        return weight_kg

    h_m = height_cm / 100.0
    ideal_weight = 25 * (h_m ** 2)
    # Adjusted weight formula: Ideal + 40% of excess
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

def soften_suppression_level(level: str, high_cv_risk: bool, high_bone_risk: bool, pregnancy: bool) -> str:
    """
    Downgrades suppression targets if patient has comorbidities or is pregnant.
    """
    # Pregnancy overrides: Avoid "Strong" suppression (<0.1) unless absolutely necessary 
    # to avoid fetal harm from maternal thyrotoxicosis, but maintain suppression for Ca.
    if pregnancy and level == "Strong":
        return "Moderate" # Shift <0.1 to 0.1-0.5 range for safety

    if not (high_cv_risk or high_bone_risk):
        return level

    order = ["None", "Mild", "Moderate", "Strong"]
    idx = order.index(level) if level in order else 1
    new_idx = max(0, idx - 1)
    return order[new_idx]

def get_tsh_targets(scenario: str, suppression_level: str, pregnancy_status: str) -> tuple[float, float]:
    """
    Returns TSH range based on Clinical Scenario + Pregnancy Trimester (ATA Guidelines).
    """
    # 1. PREGNANCY LOGIC (Overrides others)
    if pregnancy_status in ["Trimester 1", "Trimester 2", "Trimester 3"]:
        # ATA Pregnancy Targets (General consensus)
        if pregnancy_status == "Trimester 1":
            # T1: 0.1 - 2.5
            if scenario == "B": # Cancer
                return (0.1, 2.5) if suppression_level in ["None", "Mild"] else (0.1, 0.5)
            return (0.1, 2.5)
            
        elif pregnancy_status in ["Trimester 2", "Trimester 3"]:
            # T2/3: 0.2 - 3.0
            if scenario == "B":
                return (0.2, 3.0) if suppression_level in ["None", "Mild"] else (0.1, 0.5)
            return (0.2, 3.0)

    # 2. PRE-CONCEPTION PLANNING
    if pregnancy_status == "Planning Pregnancy":
        # Strict control for fertility
        return (0.5, 2.5)

    # 3. NON-PREGNANT SCENARIOS
    if scenario == "C": # Benign Hypothyroidism
        return (0.4, 4.0) # Standard normal range
    
    if scenario == "A": # Post-RAI (treat as replacement usually)
        return (0.5, 2.5)

    # Scenario B: Cancer Suppression
    level = (suppression_level or "None").capitalize()
    if level == "None": return 0.5, 2.0
    elif level == "Mild": return 0.1, 0.5
    elif level == "Moderate": return 0.1, 0.5
    elif level == "Strong": return 0.01, 0.1
    
    return 0.5, 4.0

def calculate_pregnancy_boost(current_dose: float, weight_kg: float) -> float:
    """
    Calculates the 'Pregnancy Boost' (approx 20-30% increase).
    If patient is naive (0 dose), start at 1.6 mcg/kg.
    """
    if current_dose == 0:
        return 1.6 * weight_kg # Naive start
    else:
        # ATA Recommendation: Increase by ~25-30% (e.g., 2 extra tabs per week)
        return current_dose * 1.25

def base_replacement_mcg_per_kg(age: int, high_cv_risk: bool, indication: str) -> float:
    # Cancer suppression requires higher relative doses
    base = 1.6 
    
    if indication == "Post-thyroidectomy for Ca (DTC)":
        base = 2.0 # Suppression often needs ~2.0 mcg/kg
    elif indication == "Benign Hypothyroidism":
        base = 1.6
        
    if high_cv_risk:
        return 1.0 # Cautionary start
    elif age > 60:
        return 1.4 # Elderly but healthy
    else:
        return base

def calculate_titration_suggestion(
    current_lt4: float, 
    ideal_lt4: float, 
    current_tsh: float, 
    target_tsh_high: float, 
    target_tsh_low: float,
    high_cv_risk: bool,
    pregnancy_status: str
) -> str:
    
    # PREGNANCY SPECIFIC TITRATION
    if pregnancy_status in ["Trimester 1", "Trimester 2", "Trimester 3"]:
        if current_tsh > target_tsh_high:
            return "🚨 **Pregnant & High TSH:** Increase dose IMMEDIATELY. \nATA suggests increasing current dose by ~25-30% (or add 2 extra tablets/week)."
        elif current_tsh < 0.1 and pregnancy_status == "Trimester 1":
            return "TSH suppressed in T1 is physiologically normal (hCG effect). Do not reduce dose unless FT4 is elevated."

    if current_lt4 <= 0:
        return "Initiate therapy at calculated dose."

    dose_gap = ideal_lt4 - current_lt4

    # High TSH (Under-treated)
    if current_tsh > target_tsh_high:
        if high_cv_risk:
            return f"TSH High. Safety protocol: Increase by max 12.5 mcg (Total: {current_lt4 + 12.5} mcg). Recheck 6-8 wks."
        else:
            if dose_gap > 20:
                return f"TSH High. Consider increasing by 25 mcg (Total: {current_lt4 + 25} mcg)."
            else:
                return f"TSH High. Increase by 12.5 mcg (Total: {current_lt4 + 12.5} mcg)."

    # Low TSH (Over-treated)
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
        flags.append("⚠️ **High CV Risk Protocol:** Start low, titrate slow (12.5mcg steps).")
    
    if diabetes:
        flags.append("⚠️ **Diabetes:** Monitor for silent ischemia if initiating high-dose suppression.")

    if high_bone_risk and suppression_level in ["Moderate", "Strong"]:
        flags.append("⚠️ **Bone Health:** Prolonged suppression increases fracture risk. Ensure Ca/VitD.")

    if pregnancy_status != "Non-pregnant":
        if pregnancy_status == "Planning Pregnancy":
            flags.append("🤰 **Planning Pregnancy:** Aim for TSH < 2.5 mIU/L *before* conception.")
        else:
            flags.append("🤰 **Pregnancy Protocol (ATA):** Dose requirements increase 20-50%. Check TSH every 4 weeks until mid-gestation.")

    if effective_weight_kg > 0:
        mcg_per_kg = suggested_mcg_day / effective_weight_kg
        if mcg_per_kg > 2.4:
            flags.append(f"⚠️ **High Dose Warning:** Calculated dose >2.4 mcg/kg. Rule out malabsorption or poor compliance.")

    return flags

def calculate_lt4_and_targets(inputs: dict) -> dict:
    age = inputs["age"]
    sex = inputs["sex"]
    height_cm = inputs.get("height_cm")
    weight_kg = inputs["weight_kg"]
    indication = inputs["indication"]
    preg_status = inputs["pregnancy_status"]
    
    # Comorbidities
    ischemic_hd = inputs.get("ischemic_hd", False)
    arrhythmia = inputs.get("arrhythmia", False)
    heart_failure = inputs.get("heart_failure", False)
    diabetes = inputs.get("diabetes", False)
    osteoporosis = inputs.get("osteoporosis", False)
    
    high_cv_risk = (
        age >= 60 
        or ischemic_hd 
        or arrhythmia 
        or heart_failure 
        or diabetes
    )
    
    high_bone_risk = osteoporosis or (sex == "Female" and age >= 55)

    # Define Scenario
    # A: Post-RAI (Hyper) | B: Cancer | C: Benign Hypo
    if indication == "Post-RAI for hyperthyroidism":
        scenario = "A"
    elif indication == "Post-thyroidectomy for Ca (DTC)":
        scenario = "B"
    else:
        scenario = "C"
    
    # 1. Determine Suppression Level
    if scenario == "B":
        suppression_level = map_ata_risk_and_response(
            inputs.get("initial_ata_risk"), 
            inputs.get("disease_status"), 
            inputs.get("time_since_surgery_years")
        )
        is_preg = preg_status in ["Trimester 1", "Trimester 2", "Trimester 3"]
        suppression_level = soften_suppression_level(suppression_level, high_cv_risk, high_bone_risk, is_preg)
    else:
        suppression_level = "None"

    # 2. Determine TSH Targets (Pregnancy Aware)
    tsh_low, tsh_high = get_tsh_targets(scenario, suppression_level, preg_status)

    # 3. Calculate Dosing
    effective_weight = compute_effective_weight(weight_kg, height_cm)
    base_mcg_per_kg = base_replacement_mcg_per_kg(age, high_cv_risk, indication)
    
    # Suppression Factor
    factor = 1.0
    if scenario == "B":
        if suppression_level == "Mild": factor = 1.1
        elif suppression_level == "Moderate": factor = 1.2
        elif suppression_level == "Strong": factor = 1.3
    
    suggested_dose = base_mcg_per_kg * effective_weight * factor

    # 4. Pregnancy Boost Logic
    # If pregnant, apply the "Y-Rule" (increase) regardless of scenario
    if preg_status in ["Trimester 1", "Trimester 2", "Trimester 3"]:
        current_lt4_val = inputs.get("current_lt4") or 0
        suggested_dose = calculate_pregnancy_boost(current_lt4_val if current_lt4_val > 0 else suggested_dose, effective_weight)

    # Cap for safety
    if suggested_dose > 300: suggested_dose = 300
    
    # Titration Logic
    current_tsh = inputs.get("current_tsh")
    current_lt4 = inputs.get("current_lt4")
    
    titration_note = None
    if current_tsh is not None and current_lt4 is not None:
        titration_note = calculate_titration_suggestion(
            current_lt4, suggested_dose, current_tsh, tsh_high, tsh_low, high_cv_risk, preg_status
        )

    safety_flags = build_safety_flags(
        age, high_cv_risk, high_bone_risk, preg_status, diabetes,
        suggested_dose, effective_weight, current_tsh, suppression_level
    )

    return {
        "scenario": scenario,
        "suppression_level": suppression_level,
        "tsh_target_range": (tsh_low, tsh_high),
        "ideal_calculated_dose": suggested_dose,
        "titration_note": titration_note,
        "safety_flags": safety_flags,
        "effective_weight": effective_weight
    }

# ==========================
# 3. STREAMLIT UI
# ==========================

def main():
    st.set_page_config(page_title="Thyroid Calc v2.0 (India/ATA)", layout="wide")
    
    st.title("🇮🇳 Thyroid CDSS: Pregnancy & Oncology")
    st.markdown("**Indian Standard of Care | ATA 2024/2025 Guidelines Compatible**")

    # --- SIDEBAR INPUTS ---
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
        indication = st.radio("Diagnosis", [
            "Benign Hypothyroidism", 
            "Post-thyroidectomy for Ca (DTC)", 
            "Post-RAI for hyperthyroidism"
        ])
        
        inputs = {
            "age": age, "sex": sex, "weight_kg": weight_kg, "height_cm": height_cm, 
            "indication": indication, "pregnancy_status": pregnancy_status
        }

        if indication == "Post-thyroidectomy for Ca (DTC)":
            st.subheader("Risk Stratification")
            inputs["initial_ata_risk"] = st.selectbox("Initial ATA Risk", ["Low", "Intermediate", "High"])
            inputs["disease_status"] = st.selectbox("Response to Therapy", ["Excellent", "Indeterminate", "Biochemical Incomplete", "Structural Incomplete"])
            inputs["time_since_surgery_years"] = st.number_input("Years since surgery", 0.0, 30.0, 1.0)

        st.header("3. Comorbidities")
        c1, c2 = st.columns(2)
        with c1:
            inputs["diabetes"] = st.checkbox("Diabetes")
            inputs["ischemic_hd"] = st.checkbox("Ischemic Heart Disease")
        with c2:
            inputs["arrhythmia"] = st.checkbox("Arrhythmia")
            inputs["osteoporosis"] = st.checkbox("Osteoporosis")

        st.header("4. Current Status")
        inputs["current_lt4"] = st.number_input("Current Dose (mcg)", 0.0, 300.0, 50.0, step=12.5)
        inputs["current_tsh"] = st.number_input("Current TSH (mIU/L)", 0.0, 150.0, 4.5, step=0.1)

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
            st.metric("Target TSH Range", f"{t_low} - {t_high} mIU/L")
            
            if pregnancy_status != "Non-pregnant":
                 st.info(f"**Pregnancy Context:** Range adjusted for {pregnancy_status} (ATA Guidelines).")
            elif res['scenario'] == "B":
                 st.info(f"**Oncology Context:** {res['suppression_level']} Suppression")

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
            st.caption("Brands: Thyronorm, Eltroxin, Lethyrox")
            
            # 3. Titration Advice
            if res['titration_note']:
                st.markdown("---")
                st.markdown(f"**📈 Titration Advice:**")
                st.write(res['titration_note'])
                
            # 4. Pregnancy Specific Note
            if pregnancy_status in ["Trimester 1", "Trimester 2", "Trimester 3"]:
                st.markdown("---")
                st.markdown("""
                **🤰 ATA Pregnancy Pearl:**
                If patient is already on LT4, the standard of care is to **increase the weekly dose by 20-30%** immediately. 
                *(Practical Tip: Take 2 extra tablets per week, e.g., double dose on Mon/Thu).*
                """)

if __name__ == "__main__":
    main()
