#!/usr/bin/env python
import sys
import traceback

try:
    print("Testing imports...")
    print("1. Importing extraction.llm.extract...")
    from extraction.llm.extract import parse_receipt_from_bytes
    print("   ✓ parse_receipt_from_bytes imported")
    
    print("2. Importing backend.aws_clients...")
    from backend.aws_clients import upload_file_to_s3_from_bytes
    print("   ✓ upload_file_to_s3_from_bytes imported")
    
    print("3. Importing app...")
    import app
    print("   ✓ app imported")
    
    print("\n✅ All imports successful!")
except Exception as e:
    print(f"\n❌ Import failed: {e}")
    traceback.print_exc()
    sys.exit(1)
