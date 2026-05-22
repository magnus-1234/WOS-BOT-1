import os
import re

def fix_content(content):
    # 1. Fix incorrect Union usage in re.search/match flags
    # re.IGNORECASE | re.DOTALL -> re.IGNORECASE | re.DOTALL
    content = re.sub(r'Union\[\s*re\.(\w+)\s*,\s*re\.(\w+)\s*\]', r're.\1 | re.\2', content)
    
    # 2. Fix Union usage in regex patterns (incorrectly refactored as Union[am, pm])
    # Union[am, pm] -> (am|pm) or similar
    # This is tricky without breaking type hints. We look for Union inside regex strings.
    # Pattern: r'...(a|b)...'
    content = re.sub(r"r'(.*)Union\[(\w+),\s*(\w+)\](.*)'", r"r'\1(\2|\3)\4'", content)

    # 3. Fix int type hints (Literal should be used or just int for 3.9)
    content = re.sub(r"Union\[1,\s*2\]", "int", content)

    # 4. Global type hint fix: Optional[int ] -> Optional[int]
    # Handle both : Optional[type ] and -> Optional[type ]
    content = re.sub(r':\s*([\w\[\], ]+)\s*\|\s*None', r': Optional[\1]', content)
    content = re.sub(r'->\s*([\w\[\], ]+)\s*\|\s*None', r'-> Optional[\1]', content)

    # 5. Fix dictionary merge | for Python < 3.9 compatibility (if ANY)
    # {**payload, 'key': val} -> {**payload, 'key': val}
    # We only do this for simple cases to avoid breaking bitwise OR
    content = re.sub(r'(\w+)\s*\|\s*\{([^}]+)\}', r'{**\1, \2}', content)

    return content

def main():
    paths = [
        '.',
        r'f:\STARK-whiteout survival bot'
    ]
    exclude_dirs = {'.venv', 'venv', 'bot_venv', '.venv311', '__pycache__', 'backup'}
    
    for base_path in paths:
        if not os.path.exists(base_path):
            continue
        for root, dirs, files in os.walk(base_path):
            dirs[:] = [d for d in dirs if d not in exclude_dirs]
            for file in files:
                if file.endswith('.py'):
                    filepath = os.path.join(root, file)
                    try:
                        with open(filepath, 'r', encoding='utf-8') as f:
                            content = f.read()
                        
                        new_content = fix_content(content)
                        
                        if content != new_content:
                            with open(filepath, 'w', encoding='utf-8') as f:
                                f.write(new_content)
                            print(f"Fixed: {filepath}")
                    except Exception as e:
                        print(f"Error fixing {filepath}: {e}")

if __name__ == "__main__":
    main()
