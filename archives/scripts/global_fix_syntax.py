import os
import re

def fix_file(filepath):
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Regex to find | None in type hints: e.g. ": Optional[int ]" or ": Optional[dict ]"
    # Looking for | followed by None, possibly with spaces
    # and preceded by a type name and a colon
    new_content = re.sub(r':\s*([\w\[\], ]+)\s*\|\s*None', r': Optional[\1]', content)
    
    # Also handle return type hints: -> Optional[list ]
    new_content = re.sub(r'->\s*([\w\[\], ]+)\s*\|\s*None', r'-> Optional[\1]', new_content)

    # Handle dictionary merge | if it's causing issues (Uncomment if needed)
    # new_content = re.sub(r'(\w+)\s*\|\s*\{', r'{**\1, ', new_content)

    if content != new_content:
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(new_content)
        print(f"Fixed: {filepath}")

def main():
    exclude_dirs = {'.venv', 'venv', 'bot_venv', '.venv311', '__pycache__', 'backup'}
    for root, dirs, files in os.walk('.'):
        dirs[:] = [d for d in dirs if d not in exclude_dirs]
        for file in files:
            if file.endswith('.py'):
                fix_file(os.path.join(root, file))

if __name__ == "__main__":
    main()
