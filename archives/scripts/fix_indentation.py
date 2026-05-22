import os

file_p = r"f:/Whiteout Survival Bot/cogs/bot_operations.py"
with open(file_p, 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Line 3718 is index 3717 (zero-indexed)
if len(lines) > 3717:
    line_to_fix = lines[3717]
    if "result_embed.set_footer(text=f\"Showing 20 of {len(results)} results\")" in line_to_fix:
        # Align with result_embed.add_field at 3712 (24 or 28 spaces)
        # Based on preceding logic, it should be 24 spaces (6 levels of 4) or 28 spaces (7 levels).
        # Line 3720 is '                        await modal_interaction.edit_original_response(embed=result_embed)' (24 spaces)
        lines[3717] = "                        result_embed.set_footer(text=f\"Showing 20 of {len(results)} results\")\n"
        with open(file_p, 'w', encoding='utf-8') as f:
            f.writelines(lines)
        print("Indentation fixed at line 3718.")
    else:
        print(f"ERROR: Line 3718 content did not match expected. Content: [{line_to_fix.strip()}]")
else:
    print(f"ERROR: File has fewer than 3718 lines ({len(lines)}).")
