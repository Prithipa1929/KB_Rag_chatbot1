"""Bulk indexing from the command line: python ingest.py"""
import kb

if __name__ == "__main__":
    result = kb.ingest_all()
    if not result:
        print("No documents found in company_docs/")
    for name, n in result.items():
        print(f"OK {name}: {n} chunks")
