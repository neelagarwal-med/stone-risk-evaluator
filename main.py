import streamlit as st
import spacy
import re
import requests
import pandas as pd
import os
import difflib
import time
from dotenv import load_dotenv
from recipe_scrapers import scrape_me

# --- 1. CONFIGURATION & SESSION STATE ---
load_dotenv()
USDA_API_KEY = os.getenv("USDA_API_KEY")

# Initialize Session Memory - This prevents data loss when the user interacts with widgets
if 'parsed_data' not in st.session_state:
    st.session_state.parsed_data = None
if 'bulk_results' not in st.session_state:
    st.session_state.bulk_results = None
if 'usda_cache' not in st.session_state:
    st.session_state.usda_cache = {}

def reset_app():
    """Wipes all inputs and analysis results for a clean start."""
    st.session_state.parsed_data = None
    st.session_state.bulk_results = None
    st.session_state.url_input = ""
    st.session_state.manual_input = ""

# --- 2. CLINICAL DATABASES ---
# Harvard T.H. Chan Oxalate Values (Approx mg per 100g)
HARVARD_OXALATE_DB = {
    'spinach': 755, 'rhubarb': 541, 'almonds': 122, 'beets': 275,
    'swiss chard': 645, 'cocoa powder': 262, 'sweet potato': 28,
    'peanuts': 27, 'turnips': 21, 'starfruit': 500, 'strawberries': 15,
    'potatoes': 25, 'raspberries': 15, 'blackberries': 18, 'carrots': 15,
    'sugar': 0, 'beef': 0, 'chicken': 0, 'salt': 0, 'vanilla': 0, 
    'heavy cream': 0, 'butter': 0, 'cheese': 0, 'milk': 0
}

# Exhaustive Unit Map for Kitchen Measurements to prevent "Whole" unit fallback errors
UNIT_MAP = {
    'cup': 'cup', 'cups': 'cup', 'c': 'cup', 'c.': 'cup',
    'teaspoon': 'teaspoon', 'teaspoons': 'teaspoon', 'tsp': 'teaspoon', 'tsp.': 'teaspoon', 't': 'teaspoon',
    'tablespoon': 'tablespoon', 'tablespoons': 'tablespoon', 'tbsp': 'tablespoon', 'tbsp.': 'tablespoon', 'T': 'tablespoon',
    'ounce': 'oz', 'ounces': 'oz', 'oz': 'oz', 'oz.': 'oz',
    'pound': 'lb', 'pounds': 'lb', 'lb': 'lb', 'lbs': 'lb',
    'gram': 'gram', 'grams': 'gram', 'g': 'gram', 'g.': 'gram',
    'pinch': 'pinch', 'dash': 'pinch', 'clove': 'clove'
}

# Clinical Weight Safeguards (grams per 1.0 unit) used if USDA database lacks portion weight
STD_WEIGHTS = {
    'cup': 200.0, 'tablespoon': 15.0, 'teaspoon': 5.0, 
    'oz': 28.35, 'lb': 453.6, 'gram': 1.0, 'pinch': 0.4, 'whole': 100.0
}

# --- 3. PAGE UI & STYLING ---
st.set_page_config(page_title="Stone-Risk Clinical Evaluator", page_icon="🩺", layout="wide")

st.markdown("""
    <style>
    .report-header { color: #1E3A8A; font-weight: 600; border-bottom: 2px solid #E5E7EB; padding-bottom: 10px; margin-bottom: 20px; }
    .metric-card { background-color: #F3F4F6; padding: 15px; border-radius: 8px; border-left: 5px solid #3B82F6; }
    .alert-card { background-color: #FEF2F2; padding: 15px; border-radius: 8px; border-left: 5px solid #EF4444; margin-top: 15px; }
    .success-card { background-color: #F0FDF4; padding: 15px; border-radius: 8px; border-left: 5px solid #10B981; margin-top: 15px; }
    .acidic-card { background-color: #FFFBEB; padding: 15px; border-radius: 8px; border-left: 5px solid #F59E0B; margin-top: 15px; }
    .citation { font-size: 0.85em; color: #4B5563; font-style: italic; border-top: 1px solid #E5E7EB; padding-top: 5px; margin-top: 10px; }
    </style>
""", unsafe_allow_html=True)

@st.cache_resource
def load_nlp():
    try: return spacy.load("en_core_web_sm")
    except: st.error("Run: `python -m spacy download en_core_web_sm` in terminal."); st.stop()

nlp = load_nlp()

# --- 4. ANALYTICAL BRAIN ---

def parse_ingredient_line(raw_string):
    """NLP Parser: Handles decimals, fractions, fuzzy units, and food entities."""
    # Pre-processing: Clean brackets and trailing instructions
    clean = re.sub(r'\(.*?\)', '', raw_string).split(',')[0].strip()
    clean = re.split(r'\b(plus|for)\b', clean, flags=re.IGNORECASE)[0].strip()
    
    # Advanced Quantity Regex
    quantity = 1.0
    match = re.search(r'(\d+\s+and\s+\d+/\d+|\d+\s+\d+/\d+|\d*\.\d+|\d+/\d+|\d+)', clean)
    if match:
        n_str = match.group(1).replace('and', '').strip()
        try:
            if ' ' in n_str:
                w, f = n_str.split(); n, d = f.split('/'); quantity = float(w) + (float(n)/float(d))
            elif '/' in n_str:
                n, d = n_str.split('/'); quantity = float(n)/float(d)
            else: quantity = float(n_str)
        except: quantity = 1.0

    doc = nlp(clean.lower())
    unit, food_tokens, found_unit = "whole", [], False
    
    for t in doc:
        if not found_unit:
            if t.text in UNIT_MAP:
                unit = UNIT_MAP[t.text]; found_unit = True; continue
            closest = difflib.get_close_matches(t.text, UNIT_MAP.keys(), n=1, cutoff=0.85)
            if closest:
                unit = UNIT_MAP[closest[0]]; found_unit = True; continue
        if t.pos_ in ['NOUN', 'PROPN', 'ADJ', 'VERB'] and not t.like_num:
            food_tokens.append(t.text)
            
    return {"raw_text": raw_string, "quantity": round(quantity, 2), "unit": unit, "food": " ".join(food_tokens).title()}

def get_usda_nutrients(food_entity, q_val, q_unit, api_key):
    """The analytical core for nutrient retrieval, PRAL math, and session-wide caching."""
    cache_key = f"{food_entity.lower()}_{q_unit}"
    
    # 1. Memory Check (Researcher Speed Optimization)
    if cache_key in st.session_state.usda_cache:
        base = st.session_state.usda_cache[cache_key].copy()
        for k in ['Calcium (mg)', 'Oxalate (mg)', 'Sodium (mg)', 'Protein (g)', 'PRAL (mEq)', 'Weight (g)']:
            base[k] = round(base[k] * q_val, 2)
        return base

    # 2. API Execution
    prep = ['quartered', 'chopped', 'diced', 'fresh', 'large', 'small', 'raw', 'cooked']
    query = " ".join([w for w in food_entity.split() if w.lower() not in prep])
    if not query: return None
    
    base_url = "https://api.nal.usda.gov/fdc/v1/foods/search"
    res = requests.get(f"{base_url}?query={query}&api_key={api_key}&pageSize=1&dataType=Foundation,SR%20Legacy")
    if res.status_code != 200 or not res.json().get('foods'):
        res = requests.get(f"{base_url}?query={query}&api_key={api_key}&pageSize=1&dataType=Branded")
    
    if res.status_code == 200 and res.json().get('foods'):
        f_data = res.json()['foods'][0]
        nuts = {n.get('nutrientName'): n.get('value', 0) for n in f_data.get('foodNutrients', [])}
        
        # Clinical Weight Conversion
        gw, found = 100.0, False
        for p in f_data.get('foodPortions', []):
            if q_unit in p.get('modifier', '').lower() or q_unit in p.get('measureUnit', {}).get('name', '').lower():
                amt = p.get('amount', 1.0)
                if amt == 0: amt = 1.0
                gw, found = (p.get('gramWeight', 100.0) / amt), True
                break
        if not found: gw = STD_WEIGHTS.get(q_unit, 100.0)

        # Calculate PRAL using five specific electrolytes + protein
        mult = gw / 100.0
        ca = nuts.get('Calcium, Ca', 0) * mult
        na = nuts.get('Sodium, Na', 0) * mult
        pro = nuts.get('Protein', 0) * mult
        phos = nuts.get('Phosphorus, P', 0) * mult
        pot = nuts.get('Potassium, K', 0) * mult
        mag = nuts.get('Magnesium, Mg', 0) * mult
        
        # Remer & Manz Equation
        pral = (0.49 * pro) + (0.037 * phos) - (0.021 * pot) - (0.026 * mag) - (0.013 * ca)
        
        # Oxalate Match
        ox_m = difflib.get_close_matches(food_entity.lower(), HARVARD_OXALATE_DB.keys(), n=1, cutoff=0.6)
        ox = (HARVARD_OXALATE_DB[ox_m[0]] * mult) if ox_m else 0
        
        res_obj = {"Ingredient": f_data['description'].title(), "Weight (g)": gw, "Calcium (mg)": ca, "Oxalate (mg)": ox, "Sodium (mg)": na, "Protein (g)": pro, "PRAL (mEq)": pral}
        st.session_state.usda_cache[cache_key] = res_obj
        
        final_scaled = res_obj.copy()
        for k in ['Calcium (mg)', 'Oxalate (mg)', 'Sodium (mg)', 'Protein (g)', 'PRAL (mEq)', 'Weight (g)']:
            final_scaled[k] = round(final_scaled[k] * q_val, 2)
        return final_scaled
    return None

# --- 5. MAIN UI NAVIGATION ---
st.title("🩺 Stone-Risk Clinical Evaluator")
st.markdown("Precision nutritional analysis for patient diet management and longitudinal urological research.")

with st.sidebar:
    st.header("Not Medical Advice: This is an automated parser for educational and research purposes only")
    st.header("Consult a Physician: It is not intended to diagnose, treat, or prevent any disease")
    st.header("Data Accuracy: The data is pulled from the USDA and Harvard databases; the developer is not responsible for database errors or NLP parsing inaccuracies")
    st.header("Built by Neel Agarwal in 2026")
    st.header("Email neel.agarwal@osumc.edu with any issues or feedback")
    st.button("New Assessment / Full Reset", on_click=reset_app, use_container_width=True)
    st.markdown("---")
    if USDA_API_KEY: st.success("USDA Connection: Active")
    else: st.error("USDA Key Missing: Check .env")

# --- PHASE 1: DATA INTAKE TABS ---
st.markdown("<h3 class='report-header'>Phase 1: Dietary Intake</h3>", unsafe_allow_html=True)
tab1, tab2, tab3 = st.tabs(["🌐 Recipe URL", "📝 Manual Entry", "📊 Researcher Bulk Upload"])

with tab1:
    url_box = st.text_input("Enter Recipe URL:", key="url_input")
    if st.button("Extract Data from Web"):
        if url_box:
            with st.spinner("Analyzing site structure..."):
                try:
                    scraper = scrape_me(url_box)
                    raw = scraper.ingredients()
                    bad = ['optional', 'substitute', 'recommended', 'dressing', 'serve with']
                    filtered = [i for i in raw if len(i.split()) < 15 and not any(b in i.lower() for b in bad)]
                    st.session_state.parsed_data = [parse_ingredient_line(l) for l in filtered]
                except: st.error("Site structure blocked scraper. Please use Manual Entry.")

with tab2:
    manual_box = st.text_area("Paste Ingredient List (One per line):", key="manual_input", height=150)
    if st.button("Parse Log Entry"):
        if manual_box:
            raw = [l.strip() for l in manual_box.split('\n') if l.strip()]
            st.session_state.parsed_data = [parse_ingredient_line(l) for l in raw]

with tab3:
    st.markdown("**Instructions:** Upload CSV with `record_id` and `dietary_log` (semicolon separated).")
    up_file = st.file_uploader("Upload Researcher CSV", type=["csv"])
    if up_file and st.button("Run Researcher Pipeline"):
        df_in = pd.read_csv(up_file)
        results = []
        p_bar = st.progress(0)
        for i, row in df_in.iterrows():
            rid = row.get('record_id', i)
            log = str(row.get('dietary_log', ''))
            tots = {'ca': 0, 'ox': 0, 'na': 0, 'pral': 0}
            for line in log.split(';'):
                p = parse_ingredient_line(line)
                n = get_usda_nutrients(p['food'], p['quantity'], p['unit'], USDA_API_KEY)
                if n:
                    tots['ca'] += n['Calcium (mg)']; tots['ox'] += n['Oxalate (mg)']
                    tots['na'] += n['Sodium (mg)']; tots['pral'] += n['PRAL (mEq)']
            results.append({'record_id': rid, 'ca_mg': round(tots['ca']), 'ox_mg': round(tots['ox']), 'na_mg': round(tots['na']), 'pral_mEq': round(tots['pral'], 2)})
            p_bar.progress((i + 1) / len(df_in))
        st.session_state.bulk_results = pd.DataFrame(results)

# --- PHASE 2: VERIFICATION & CLINICAL REPORTING ---
if st.session_state.bulk_results is not None:
    st.markdown("<h3 class='report-header'>Bulk Cohort Analysis</h3>", unsafe_allow_html=True)
    st.dataframe(st.session_state.bulk_results, use_container_width=True)
    csv_bytes = st.session_state.bulk_results.to_csv(index=False).encode('utf-8')
    st.download_button("📥 Export Results", data=csv_bytes, file_name="research_results.csv", mime="text/csv")

elif st.session_state.parsed_data is not None:
    st.markdown("<h3 class='report-header'>Phase 2: Verification & Analysis</h3>", unsafe_allow_html=True)
    
    # 1. Portion Logic
    st.markdown("#### Portion & Consumption Controls")
    c_yield, c_eaten = st.columns(2)
    with c_yield: yield_amt = st.number_input("Total servings in recipe:", min_value=1.0, value=1.0)
    with c_eaten: eaten_amt = st.number_input("Servings consumed by patient:", min_value=0.1, value=1.0)
    portion_multiplier = eaten_amt / yield_amt

    # 2. Safeguard Alerts
    suspicious = [f"{p['quantity']} {p['unit']} of {p['food']}" for p in st.session_state.parsed_data if (p['quantity'] > 10 and p['unit'] != 'gram') or p['quantity'] > 2000]
    if suspicious: st.error("🚩 **Outlier Alert:** Quantities look unusual. Verify the table below.\n" + "\n".join([f"- {s}" for s in suspicious]))
    else: st.info("💡 **Clinician Review:** Modify, add, or delete items below. Changes update the report calculation.")
    
    # 3. Dynamic Data Editor (Adds Add/Delete capability)
    edited_df = st.data_editor(
        pd.DataFrame(st.session_state.parsed_data), 
        column_config={
            "raw_text": "Source Text", 
            "quantity": st.column_config.NumberColumn("Qty", min_value=0.0), 
            "unit": st.column_config.SelectboxColumn("Unit", options=list(set(UNIT_MAP.values()))),
            "food": "Food Item (Editable)"
        }, 
        disabled=["raw_text"], 
        num_rows="dynamic",
        hide_index=True, 
        use_container_width=True
    )
    
    # 4. Final Calculation Button
    if st.button("Generate Final Clinical Report", type="primary", use_container_width=True):
        final_res, final_tots = [], {'ca': 0, 'ox': 0, 'na': 0, 'pral': 0}
        for _, row in edited_df.iterrows():
            if not row['food']: continue # Safety check for newly added empty rows
            n = get_usda_nutrients(row['food'], row['quantity'], row['unit'], USDA_API_KEY)
            if n:
                for k in ['ca', 'ox', 'na', 'pral']: 
                    # Multiply each nutrient by the portion consumed
                    key_map = {'ca': 'Calcium (mg)', 'ox': 'Oxalate (mg)', 'na': 'Sodium (mg)', 'pral': 'PRAL (mEq)'}
                    final_tots[k] += (n[key_map[k]] * portion_multiplier)
                
                # Create scaled ledger entry
                scaled = n.copy()
                for k in ['Calcium (mg)', 'Oxalate (mg)', 'Sodium (mg)', 'Protein (g)', 'PRAL (mEq)', 'Weight (g)']: 
                    scaled[k] = round(scaled[k] * portion_multiplier, 2)
                final_res.append(scaled)

        # --- RESULTS DASHBOARD ---
        st.markdown(f"<br><h3 class='report-header'>Patient Intake Profile ({eaten_amt} of {yield_amt} servings)</h3>", unsafe_allow_html=True)
        ratio = final_tots['ca'] / final_tots['ox'] if final_tots['ox'] > 0 else float('inf')
        p_val = round(final_tots['pral'], 1)
        
        c1, c2, c3, c4 = st.columns(4)
        c1.markdown(f"<div class='metric-card'><strong>Calcium Intake</strong><br><h2>{round(final_tots['ca'])} mg</h2></div>", unsafe_allow_html=True)
        c2.markdown(f"<div class='metric-card'><strong>Oxalate Intake</strong><br><h2>{round(final_tots['ox'])} mg</h2></div>", unsafe_allow_html=True)
        c3.markdown(f"<div class='metric-card'><strong>Ca:Ox Ratio</strong><br><h2>{round(ratio, 2)}</h2></div>", unsafe_allow_html=True)
        c4.markdown(f"<div class='metric-card'><strong>Sodium Load</strong><br><h2>{round(final_tots['na'])} mg</h2></div>", unsafe_allow_html=True)
        
        # PRAL SECTION
        p_col = "#991B1B" if p_val > 0 else "#065F46"
        st.markdown(f"<br><div class='metric-card'><strong>Net Potential Renal Acid Load (PRAL)</strong><br><h2 style='color:{p_col};'>{p_val} mEq</h2></div>", unsafe_allow_html=True)
        
        if p_val > 0:
            severity = "High" if p_val > 10 else "Mild"
            st.markdown(f"""
            <div class='acidic-card'>
                <h4 style='margin:0;'>🧪 {severity} Acid Load Alert</h4>
                This meal produces a net acid load, lowering urinary pH. Consistently acidic urine (< 5.5) is a primary driver for uric acid and calcium oxalate crystallization.
                <br><br><strong>Recommendation:</strong> Pair acidic meals with alkali-rich citrus (lemon juice) or prescribed citrate buffering.
            </div>""", unsafe_allow_html=True)
        else:
            st.markdown("<div class='success-card' style='margin-top:15px;'>🧪 **Alkali Protective Load:** This intake raises urinary pH, helping neutralize crystallization risks.</div>", unsafe_allow_html=True)

        # BINDING SECTION
        if final_tots['ox'] > 50:
            if ratio < 1.0:
                deficit = final_tots['ox'] - final_tots['ca']
                st.markdown(f"""
                <div class='alert-card'>
                    <h4 style='margin:0;'>⚠️ Enteric Binding Deficit</h4>
                    The portion consumed contains high oxalate without sufficient calcium to bind it in the gut.
                    <br><br><strong>Clinical Prescription:</strong> Add <strong>{round(deficit)} mg Calcium</strong> (e.g., {round(deficit/200, 1)} oz cheese) to this specific meal to prevent renal oxalate excretion.
                </div>""", unsafe_allow_html=True)
            else:
                st.markdown("<div class='success-card'>✅ **Optimal Enteric Binding:** Sufficient calcium is present to bind the consumed oxalate load.</div>", unsafe_allow_html=True)

        # LEDGER & CITATIONS
        st.subheader("Analytical Ledger (Scaled per serving eaten)")
        st.dataframe(pd.DataFrame(final_res), use_container_width=True)
        
        with st.expander("🔬 View Detailed Clinical Context & Guidelines", expanded=False):
            st.markdown(r"""
            ### Urological Guidelines & Patient Context
            This report evaluates variables recognized by the **American Urological Association (AUA)**. 
            
            * **Calcium-Oxalate Binding:** Contrary to outdated advice, dietary calcium restriction increases stone risk. Normal dietary calcium (1,000–1,200 mg/day) binds with dietary oxalate in the GI tract, preventing absorption.
                <br><span class='citation'>*Citation: Borghi L, et al. Comparison of two diets for the prevention of recurrent stones. N Engl J Med. 2002.*</span>
                
            * **Sodium Management:** High sodium intake directly inhibits renal calcium reabsorption. AUA recommends stone formers limit sodium to < 2,300 mg/day.
                <br><span class='citation'>*Source: [AUA Guidelines](https://www.auanet.org/guidelines-and-quality/guidelines/kidney-stones-medical-mangement-guideline)*</span>

            * **PRAL Math:** Predicts urine pH load using the Remer and Manz equation:
                $$PRAL = 0.49 \times Protein + 0.037 \times Phosphorus - 0.021 \times Potassium - 0.026 \times Magnesium - 0.013 \times Calcium$$
            """, unsafe_allow_html=True)