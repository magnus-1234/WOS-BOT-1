import os
import re

file_p = r"f:/Whiteout Survival Bot/cogs/bot_operations.py"
with open(file_p, 'r', encoding='utf-8') as f:
    lines = f.readlines()

new_lines = []
fix_count = 0

for i in range(len(lines)):
    line = lines[i]
    new_lines.append(line)
    
    # Check if this line is an 'if' or 'for' or 'async for' or 'elif' or 'else'
    stripped = line.strip()
    if stripped.endswith(':') and (
        stripped.startswith('if ') or 
        stripped.startswith('for ') or 
        stripped.startswith('async for ') or 
        stripped.startswith('elif ') or 
        stripped.startswith('else:')
    ):
        # Check the next non-empty line
        j = i + 1
        while j < len(lines) and not lines[j].strip():
            j += 1
        
        if j < len(lines):
            next_line = lines[j]
            current_indent = len(line) - len(line.lstrip())
            next_indent = len(next_line) - len(next_line.lstrip())
            
            # If next line is not indented AND it's a set_footer call
            if next_indent <= current_indent and '.set_footer(' in next_line:
                # We'll fix it in the next iteration or here? 
                # Let's actually modify the lines list directly and restart/continue
                pass

# Let's try a different approach: line by line with state
fixed_lines = []
for i in range(len(lines)):
    line = lines[i]
    if i > 0:
        prev_line = lines[i-1].strip()
        if prev_line.endswith(':') and (
            prev_line.startswith('if ') or 
            prev_line.startswith('for ') or 
            prev_line.startswith('async for ') or 
            prev_line.startswith('elif ') or 
            prev_line.startswith('else:')
        ):
            # Current line should be indented
            current_indent = len(line) - len(line.lstrip())
            prev_indent = len(lines[i-1]) - len(lines[i-1].lstrip())
            if current_indent <= prev_indent and '.set_footer(' in line:
                # Add 4 spaces to current line
                line = "    " + line
                fix_count += 1
    fixed_lines.append(line)

if fix_count > 0:
    with open(file_p, 'w', encoding='utf-8') as f:
        f.writelines(fixed_lines)
    print(f"Fixed {fix_count} indentation errors in bot_operations.py.")
else:
    print("No indentation errors found to fix.")
