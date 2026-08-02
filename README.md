# USPS Tracking Analyzer (Production Release)

A powerful, high-volume Windows desktop application designed to audit USPS tracking numbers. It automates a real browser to verify scan history and identifies "No Scan" labels that never entered the USPS network.

---

## 🚀 Quick Start for Users

1.  **Download:** Get the latest `USPS_Tracking_Analyzer.exe` from the [Releases](https://github.com/YOUR_USERNAME/USPS_Tracking_Analyzer/releases) page.
2.  **Browser Setup:** The first time you run the app, you must install the browser engine. Open a terminal (PowerShell) and run:
    ```bash
    pip install playwright
    playwright install chromium
    ```
3.  **Run:** Double-click the `.exe` file to start auditing.

---

## 🛠 Features

-   **Intelligent Import:** Supports `.xlsx`, `.csv`, and `.xls`. Auto-detects columns.
-   **Live Monitoring:** Real-time progress, ETA, and activity logs.
-   **Safe Automation:** Pauses for manual CAPTCHA resolution to respect USPS terms.
-   **Categorized Reports:** Automatically generates Scanned, No Scan, and Failed reports.
-   **Dark Mode:** Modern, professional UI built with CustomTkinter.

---

## 🏗 Developer Build Instructions

If you want to build the executable yourself:

```bash
# 1. Setup environment
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
pip install pyinstaller

# 2. Build the EXE
pyinstaller --noconsole --onefile --add-data "assets;assets" --name "USPS_Tracking_Analyzer" main.py
```
The final file will be in the `dist/` folder.

---

## 📁 Project Structure

- `main.py`: Entry point.
- `tracker.py`: Playwright automation core.
- `gui.py`: CustomTkinter interface.
- `excel_handler.py`: Data cleaning and column detection.
- `exporter.py`: Excel report generation.

---

## ⚖ Legal Note

This tool automates public tracking lookups. It does not bypass security, solve CAPTCHAs programmatically, or access private APIs. Users are responsible for complying with USPS's Terms of Service.
