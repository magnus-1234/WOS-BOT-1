import re

def fix_file(filepath, encoding):
    try:
        with open(filepath, 'r', encoding=encoding) as f:
            content = f.read()
    except Exception as e:
        print(f"Error reading {filepath}: {e}")
        return

    # Remove all .live-board::before blocks in cyberpunk theme
    content = re.sub(r'\[data-theme="cyberpunk-cool"\]\s*\.live-board::before\s*\{[^}]*\}', '', content)
    
    # Replace avatar-snapshot img in cyberpunk theme
    content = re.sub(
        r'\[data-theme="cyberpunk-cool"\]\s*\.avatar-snapshot img\s*\{[^}]*\}',
        '[data-theme="cyberpunk-cool"] .avatar-snapshot img {\n  border: 2px solid #00FFFF !important;\n  filter: none !important;\n}',
        content
    )
    
    with open(filepath, 'w', encoding=encoding) as f:
        f.write(content)
    print(f"Fixed {filepath}")

fix_file(r'f:\Whiteout Survival Bot\old_site.css', 'utf-16le')
fix_file(r'f:\Whiteout Survival Bot\frontend-dashboard\assets\site.css', 'utf-8')
fix_file(r'f:\Whiteout Survival Bot\cyberpunk_rules.css', 'utf-8')
