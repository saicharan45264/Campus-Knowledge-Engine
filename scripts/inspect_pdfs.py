import pdfplumber
import sys

def inspect_pdf(path):
    print(f"\n--- Inspecting {path} ---")
    with pdfplumber.open(path) as pdf:
        for i, page in enumerate(pdf.pages[:2]):
            print(f"Page {i+1} Tables:")
            tables = page.extract_tables()
            if tables:
                for t in tables:
                    for row in t[:5]:  # print first 5 rows of each table
                        print(row)
            else:
                print("No tables found. First 500 chars:")
                print(page.extract_text()[:500])

inspect_pdf("/Users/saicharanboddeti/Downloads/sample_timetable.pdf")
inspect_pdf("/Users/saicharanboddeti/Downloads/Academic-Calendar-AY-2026-2027 (1).pdf")
