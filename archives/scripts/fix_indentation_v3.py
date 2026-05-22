import os
import re

file_p = r"f:/Whiteout Survival Bot/cogs/bot_operations.py"
with open(file_p, 'r', encoding='utf-8') as f:
    lines = f.readlines()

new_lines = []
fix_count = 0

# Pattern for embed variable assignment or use
# e.g. processing_embed = discord.Embed(...)
# e.g. processing_embed.add_field(...)
# We want to match the indentation of these lines and apply to .set_footer calls on the same variable.

embed_vars = {}

for i, line in enumerate(lines):
    # Detect variable assignment to discord.Embed
    match_assign = re.match(r"^(\s+)(\w+)\s*=\s*discord\.Embed\(", line)
    if match_assign:
        indent, var_name = match_assign.groups()
        embed_vars[var_name] = indent
    
    # Detect .set_footer call
    match_footer = re.match(r"^(\s+)(\w+)\.set_footer\(", line)
    if match_footer:
        current_indent, var_name = match_footer.groups()
        if var_name in embed_vars:
            target_indent = embed_vars[var_name]
            if current_indent != target_indent:
                # Fix the line
                new_line = target_indent + var_name + ".set_footer(" + line.split(".set_footer(", 1)[1]
                new_lines.append(new_line)
                fix_count += 1
                continue
    
    new_lines.append(line)

if fix_count > 0:
    with open(file_p, 'w', encoding='utf-8') as f:
        f.writelines(new_lines)
    print(f"Fixed {fix_count} indentation errors in bot_operations.py via variable matching.")
else:
    print("No errors found to fix via variable matching.")
