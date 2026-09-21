---
name: financial-report-analyzer
description: In-depth analysis of listed-company financial reports (such as annual and quarterly reports). This skill automatically extracts key financial metrics, calculates core financial ratios, generates visualizations, and produces professional financial analysis reports with industry context.
---

# Financial Report Analyzer

This skill helps DB-GPT systematically analyze listed-company financial reports by extracting core data, calculating financial ratios, generating visualizations, and incorporating business context to produce high-quality financial analysis reports.

## Core Workflow

1. **Data extraction and structuring**:
   - Use the `execute_skill_script_file` tool to run the `scripts/extract_financials.py` script, passing the report file path (`file_path` parameter) to automatically extract core figures such as revenue, net profit, assets, and liabilities.
   - The script supports PDF files (parsed via pdfplumber) and plain-text files, and returns structured data in JSON format.

2. **Financial ratio calculation**:
   - Use `execute_skill_script_file` to run `scripts/calculate_ratios.py`, passing the JSON data from Step 1.
   - Automatically calculate key metrics such as gross margin, net margin, ROE, and debt-to-asset ratio, outputting 30 template placeholder keys.
   - Refer to `references/financial_metrics.md` to ensure accurate metric definitions.
   - **The system automatically saves the returned JSON result** (`react_state["ratio_data"]`), which html_interpreter merges automatically later.

3. **Chart generation**:
   - Use `execute_skill_script_file` to run `scripts/generate_charts.py`, passing the JSON data from Step 1.
   - Automatically generate 3 visualizations:
     - `financial_overview.png`: bar chart comparing core financial metrics
     - `profitability.png`: horizontal bar chart of profitability metrics
     - `asset_structure.png`: donut chart of asset structure
   - **The system automatically copies images to the static directory and records the URL mapping** (`react_state["image_url_map"]`), which html_interpreter merges automatically later.

4. **In-depth analysis**:
   - Follow the framework in `references/analysis_framework.md` to analyze in depth across four dimensions: earnings quality, solvency risk, operating efficiency, and cash flow.
   - Combine with the "Management Discussion and Analysis" section to explain the key drivers of performance changes.
   - Write the following 7 analysis passages:
     - `PROFITABILITY_ANALYSIS`: profitability analysis
     - `SOLVENCY_ANALYSIS`: solvency and risk analysis
     - `EFFICIENCY_ANALYSIS`: operating efficiency analysis
     - `CASHFLOW_ANALYSIS`: cash flow and earnings-quality analysis
     - `ADVANTAGES_LIST`: list of key strengths (HTML `<li>` format)
     - `RISKS_LIST`: list of key risks (HTML `<li>` format)
     - `OVERALL_ASSESSMENT`: overall assessment

5. **Render the report**:
   - Call `html_interpreter` using `template_path` mode:
     ```json
     {
       "template_path": "financial-report-analyzer/templates/report_template.html",
       "data": {
         "PROFITABILITY_ANALYSIS": "Profitability analysis written by the LLM...",
         "SOLVENCY_ANALYSIS": "Solvency analysis written by the LLM...",
         "EFFICIENCY_ANALYSIS": "Operating efficiency analysis written by the LLM...",
         "CASHFLOW_ANALYSIS": "Cash flow analysis written by the LLM...",
         "ADVANTAGES_LIST": "<li>Strength 1</li><li>Strength 2</li>",
         "RISKS_LIST": "<li>Risk 1</li><li>Risk 2</li>",
         "OVERALL_ASSESSMENT": "Overall assessment written by the LLM..."
       },
       "title": "XX Company 2023 Annual Financial Report Analysis"
     }
     ```
   - **Important**: only pass the 7 analysis passages you wrote in the `data` dictionary! The backend automatically merges:
     - The 30 data metrics from Step 2 (COMPANY_NAME, REVENUE, NET_PROFIT, etc.)
     - The chart URLs from Step 3 (CHART_FINANCIAL_OVERVIEW, CHART_PROFITABILITY, CHART_ASSET_STRUCTURE)
   - **Never** include data metrics or chart paths in `data`; otherwise the JSON will be too large and get truncated.

6. **Complete**:
   - Call `terminate` to return a brief 1-2 sentence summary.
   - The report is displayed as a card in the left panel; users click the card to view the full report in the right panel.

## Complete Workflow Example

```
Step 1: execute_skill_script_file(skill_name="financial-report-analyzer", script_file_name="extract_financials.py", args={"file_path": "/path/to/report.pdf"})
  → Returns JSON: {"revenue": 10500000000, "net_profit": 1200000000, ...}  (saved as raw_data)

Step 2: execute_skill_script_file(skill_name="financial-report-analyzer", script_file_name="calculate_ratios.py", args=<raw_data>)
  → Returns 30 template keys, automatically recorded in react_state["ratio_data"]

Step 3: execute_skill_script_file(skill_name="financial-report-analyzer", script_file_name="generate_charts.py", args=<raw_data>)
  → Generates charts, automatically copied to /images/ with URL mapping recorded

Step 4: (LLM writes the 7 in-depth analysis passages)

Step 5: html_interpreter(template_path="financial-report-analyzer/templates/report_template.html", data={only the 7 analysis passages}, title="report title")
  → Backend automatically merges data metrics + chart URLs + analysis text to render the complete report

Step 6: terminate(result="brief summary")
```

## Resource Usage

- **Scripts** (all executed via `execute_skill_script_file`):
  - `scripts/extract_financials.py`: accepts the `file_path` parameter, reads the financial report file (PDF and text formats supported), and extracts core financial data.
  - `scripts/calculate_ratios.py`: computes financial ratios and outputs 30 template placeholder keys. The system records the result automatically.
  - `scripts/generate_charts.py`: generates 3 visualizations (matplotlib); the system handles image copying automatically.
  - `scripts/fill_template.py`: (fallback) accepts three parameters — `ratio_data`, `chart_paths`, and `analysis` — reads the HTML template and replaces all placeholders. Normally this script is not needed because html_interpreter's template_path mode fills the template automatically.
- **References**:
  - `references/financial_metrics.md`: contains formula definitions.
  - `references/analysis_framework.md`: contains the analysis logic.
- **Templates**:
  - `templates/report_template.html`: the HTML template for the final report (**must be followed strictly**; do not remove sections or modify table structure). It is automatically read and filled via html_interpreter's template_path parameter.
  - `templates/report_template.md`: Markdown version, provided for structural reference only.

## Notes

- **You must use `execute_skill_script_file`** to run scripts (do not use shell_interpreter), because `execute_skill_script_file` automatically handles image copying and data recording.
- Script extraction may be affected by formatting; manually verify the extracted key figures before computing ratios.
- Always check "non-recurring gains and losses" to assess the true profitability of the core business.
- Compare at least three years of historical data to identify trends.
- `generate_charts.py` depends on matplotlib; make sure the library is installed in the environment.
