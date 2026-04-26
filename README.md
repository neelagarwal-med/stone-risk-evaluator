# stone-risk-evaluator
🩺 Stone-Risk Clinical Evaluator
Automated Lithogenic Dietary Profiling & High-Throughput Research Engine

Evaluating a patient's dietary risk for nephrolithiasis (kidney stones) has historically been a manual, time-consuming process involving fragmented databases and complex biochemistry math.

The Stone-Risk Clinical Evaluator is an end-to-end informatics utility that transforms unstructured dietary logs—from web recipes to raw patient transcripts—into structured, actionable clinical data. By integrating Natural Language Processing (NLP) with the USDA FoodData Central and the Harvard T.H. Chan Oxalate Database, this tool quantifies the variables that matter most for stone prevention.

🚀 Key Features
1. Clinical Decision Support (Patient/Clinician UI)

NLP Ingredient Parser: Point the tool at any recipe URL or paste a raw log. Our spaCy-powered engine extracts quantities, units, and food entities automatically.

Enteric Binding Logic: Calculates the Calcium-to-Oxalate ratio in real-time. If a meal is high-risk, the app generates a dynamic "Dietary Prescription" specifying exactly how many milligrams of calcium are needed to buffer the oxalate load.

PRAL & Urinary pH Prediction: Implements the Remer and Manz equation to calculate Potential Renal Acid Load (PRAL), identifying meals that acutely lower urinary pH and drive crystallization.

Dynamic Portion Control: Scalable servings math allows patients to specify exactly what they consumed from a multi-serving recipe.

2. The Research Pipeline (High-Throughput Mode)

Bulk CSV Processing: Researchers can upload cohort-scale datasets (thousands of entries) for automated analysis.

Intelligent Caching: Implements a session-wide memory cache. The engine "remembers" previously queried items, preventing API throttling and reducing processing time from hours to seconds.

Standardized Output: Generates an "Enriched Dataset" with precise columns for Total Calcium, Oxalate, Sodium, and Net PRAL—ready for population-level statistical analysis in R or Stata.

🔬 Scientific Foundation
This tool adheres to guidelines established by the American Urological Association (AUA) and peer-reviewed literature:

Enteric Oxalate Binding: Based on the landmark study by Borghi L, et al. (NEJM 2002), which established that normal dietary calcium intake is more protective against stones than calcium restriction.

PRAL Math: Calculated using the formula:

PRAL=0.49×Protein+0.037×Phosphorus−0.021×Potassium−0.026×Magnesium−0.013×Calcium
Sodium Management: Flags intakes exceeding the AUA-recommended < 2,300 mg/day threshold.

🛠️ Tech Stack & Safety
Language: Python 3.10+

Framework: Streamlit (UI), spaCy (NLP)

APIs: USDA FoodData Central

Clinical Guardrails: Includes an outlier detection system to identify and flag physically impossible quantities (e.g., preventing typos like "100 lbs of spinach") before they skew clinical reports.

⚙️ Quick Start
1. Installation

Bash
pip install -r requirements.txt
python -m spacy download en_core_web_sm
2. Configuration

Create a .env file in the root directory:

Plaintext
USDA_API_KEY=your_key_here
3. Execution

Bash
streamlit run app.py
📁 Repository Structure
app.py: The main clinical engine and Streamlit interface.

.env: Local environment variables (ignored by Git).

.gitignore: Aggressive medical-grade data protection to prevent accidental upload of patient CSVs.

requirements.txt: Standardized dependency tracking for reproducibility.

⚖️ Medical Disclaimer
This tool is provided for educational and research purposes only. It is not a substitute for professional medical advice, diagnosis, or treatment. It is intended for use by clinical researchers and healthcare professionals to assist in dietary evaluation workflows.
