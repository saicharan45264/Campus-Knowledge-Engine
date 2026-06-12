import pdfplumber
import pprint

def inspect_timetable(path):
    print(f"\n--- Inspecting {path} ---")
    with pdfplumber.open(path) as pdf:
        for i, page in enumerate(pdf.pages):
            print(f"Page {i+1} Tables:")
            tables = page.extract_tables()
            if tables:
                for t in tables:
                    for row in t:
                        print(row)
            else:
                print("No tables found. First 500 chars:")
                print(page.extract_text()[:500])

inspect_timetable("/Users/saicharanboddeti/Downloads/sample_timetable.pdf")
