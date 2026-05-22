import os
import re

cogs_dir = r'f:\Whiteout Survival Bot\cogs'

# Regex for finding set_footer(...) possibly multi-line
# This matches set_footer( and then everything up to the closing )
# We use re.DOTALL to match across newlines
pattern = re.compile(r'\.set_footer\s*\((.*?)\)', re.DOTALL)

def standardize_footer(match):
    # If the original footer has "Page" in it, we might want to keep it?
    # Actually, user said: consistent footer: Whiteout Survival | Magnus.
    # We will replace it entirely.
    return '.set_footer(text="Whiteout Survival | Magnus")'

files_processed = 0
footers_replaced = 0

for root, dirs, files in os.walk(cogs_dir):
    for name in files:
        if name.endswith('.py'):
            file_path = os.path.join(root, name)
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            new_content = pattern.sub(standardize_footer, content)
            
            if new_content != content:
                with open(file_path, 'w', encoding='utf-8') as f:
                    f.write(new_content)
                files_processed += 1
                footers_replaced += len(pattern.findall(content))

print(f"Branding update complete. Processed {files_processed} files and replaced {footers_replaced} footers.")
