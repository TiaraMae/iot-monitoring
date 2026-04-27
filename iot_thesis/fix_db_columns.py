import os
import re

filepath = 'app.py'

if os.path.exists(filepath):
    with open(filepath, 'r', encoding='utf-8') as f:
        code = f.read()

    # We need to replace the SQL column references of "subtype" to "sub_type"
    
    # 1. In get_appliances_for_user
    code = code.replace("created_at, operational_status, subtype", "created_at, operational_status, sub_type")
    
    # 2. In do_set_baseline_calculated
    code = code.replace("SELECT subtype, type", "SELECT sub_type, type")
    
    # 3. In export_excel
    code = code.replace("SELECT name, type, subtype, operational_status", "SELECT name, type, sub_type, operational_status")
    
    # 4. In api_spc_limits
    code = code.replace("SELECT type, subtype,", "SELECT type, sub_type,")
    
    # 5. In pair_device (Insert statement)
    code = code.replace(
        "INSERT INTO appliances (user_id, name, type, location, brand, operational_status, subtype, icompressor_offset)",
        "INSERT INTO appliances (user_id, name, type, location, brand, operational_status, sub_type, icompressor_offset)"
    )

    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(code)

    print("✅ Fixed! Updated app.py to use 'sub_type' instead of 'subtype'.")
else:
    print("app.py not found in the current folder.")