import json
import os

filename = "test_data.json"
test_data = {
    "status": "success",
    "message": "JSON read/write is working properly!",
    "catalog_size": 20,
    "features": [
        "Structure it",
        "Score it",
        "Match it",
        "Gate it",
        "Prove it moved"
    ]
}

print(f"Testing JSON write to '{filename}'...")
try:
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(test_data, f, indent=4)
    print("Write operation successful.")
except Exception as e:
    print(f"Error writing JSON file: {e}")
    exit(1)

print(f"Testing JSON read from '{filename}'...")
try:
    with open(filename, "r", encoding="utf-8") as f:
        loaded_data = json.load(f)
    print("Read operation successful.")
    
    print("\n--- Loaded JSON Data ---")
    print(json.dumps(loaded_data, indent=2))
    print("------------------------\n")
    
    # Assert correctness
    if loaded_data.get("status") == "success" and len(loaded_data.get("features", [])) == 5:
        print("Data validation check passed!")
        print("JSON read/write test successful!")
    else:
        print("Data validation check failed. Data was corrupted.")
        
except Exception as e:
    print(f"Error reading JSON file: {e}")
    exit(1)

# Clean up
try:
    if os.path.exists(filename):
        os.remove(filename)
        print(f"Temporary file '{filename}' cleaned up successfully.")
except Exception as e:
    print(f"Error cleaning up temporary file: {e}")
