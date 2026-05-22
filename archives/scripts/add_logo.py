import os
import re
from pathlib import Path

def add_logo_to_footers():
    cogs_dir = Path("f:/Whiteout Survival Bot/cogs")
    logo_url = "https://cdn.discordapp.com/attachments/1435569370389807144/1445459239131680859/images_7_1.png"
    
    # Matches set_footer(text="Whiteout Survival | Magnus") specifically when NO icon_url is present
    # We use a negative lookahead to ensure icon_url is not already on that line
    pattern = re.compile(r'set_footer\(text="Whiteout Survival\s*\|\s*Magnus"\)(?![^)\n]*icon_url)')
    
    replacement = f'set_footer(text="Whiteout Survival | Magnus", icon_url="{logo_url}")'
    
    modified_files = []
    
    for file_path in cogs_dir.glob("*.py"):
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            if 'set_footer(text="Whiteout Survival | Magnus")' in content:
                new_content = pattern.sub(replacement, content)
                
                if new_content != content:
                    with open(file_path, 'w', encoding='utf-8') as f:
                        f.write(new_content)
                    modified_files.append(file_path.name)
                    print(f"✅ Added logo to footers in {file_path.name}")
        except Exception as e:
            print(f"❌ Error processing {file_path.name}: {e}")
            
    return modified_files

if __name__ == "__main__":
    repaired = add_logo_to_footers()
    print(f"\n--- Total Files Updated with Logo: {len(repaired)} ---")
