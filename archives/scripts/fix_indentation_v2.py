import os

file_p = r"f:/Whiteout Survival Bot/cogs/bot_operations.py"
with open(file_p, 'r', encoding='utf-8') as f:
    lines = f.readlines()

new_lines = []
fix_count = 0

for line in lines:
    original_line = line
    # Match the over-indented pattern (44 spaces)
    if "                                            result_embed.set_footer(text=f\"Showing 20 of {len(results)} results\")" in line:
        # Replace with 24 spaces
        new_lines.append("                        result_embed.set_footer(text=f\"Showing 20 of {len(results)} results\")\n")
        fix_count += 1
    # Match another potentially over-indented pattern seen earlier (line 2235 - note: I already fixed it but let's be safe)
    elif "                                        processing_embed.set_footer(text=f\"Processing 0/{len(fid_list)} FIDs...\")" in line:
        new_lines.append("                                processing_embed.set_footer(text=f\"Processing 0/{len(fid_list)} FIDs...\")\n")
        fix_count += 1
    # Match line 2268 over-indented pattern
    elif "                                                progress_embed.set_footer(text=f\"Processing {idx}/{len(fid_list)} FIDs...\")" in line:
        new_lines.append("                                        progress_embed.set_footer(text=f\"Processing {idx}/{len(fid_list)} FIDs...\")\n")
        fix_count += 1
    else:
        new_lines.append(line)

if fix_count > 0:
    with open(file_p, 'w', encoding='utf-8') as f:
        f.writelines(new_lines)
    print(f"Fixed {fix_count} indentation errors in bot_operations.py.")
else:
    print("No errors found to fix (already fixed or pattern mismatch).")
