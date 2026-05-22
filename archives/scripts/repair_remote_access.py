import sys

file_path = r'f:\Whiteout Survival Bot\cogs\remote_access.py'

with open(file_path, 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Find the corrupted section
# Looking for line 1183 (label="◀ Back to Channel Sele)
start_idx = -1
for i, line in enumerate(lines):
    if 'label="◀ Back to Channel Sele' in line:
        start_idx = i
        break

if start_idx == -1:
    print("Could not find corrupted line.")
    sys.exit(1)

# We want to replace from start_idx up to the "except Exception as e" block of play_music
# which is around line 1193 in the current mangled state.
end_idx = -1
for i in range(start_idx, min(start_idx + 30, len(lines))):
    if 'await interaction.response.send_message(' in lines[i] and 'setting up music playback' in lines[i+1]:
        end_idx = i + 2 # Include the closing parenthesis and potentially the except block
        break

if end_idx == -1:
    # Try looking for the next method start as an anchor
    for i in range(start_idx, min(start_idx + 50, len(lines))):
        if 'async def start_alliance_monitor' in lines[i]:
            end_idx = i
            break

if end_idx == -1:
    print("Could not find end of corrupted section.")
    sys.exit(1)

print(f"Replacing lines {start_idx+1} to {end_idx}")

new_content = [
    '                label="◀ Back to Channel Selection",\n',
    '                style=discord.ButtonStyle.secondary\n',
    '            )\n',
    '            back_button.callback = lambda i: self.send_message(i, guild)\n',
    '            view.add_item(back_button)\n',
    '            \n',
    '            await interaction.response.edit_message(embed=embed, view=view)\n',
    '            \n',
    '        except Exception as e:\n',
    '            print(f"Show message type error: {e}")\n',
    '            import traceback\n',
    '            traceback.print_exc()\n',
    '            await interaction.response.send_message(\n',
    '                "❌ An error occurred while loading message options.",\n',
    '                ephemeral=True\n',
    '            )\n'
]

# The corruption might have left some half-finished try/except blocks.
# Let's see what's currently there.
# The original 'try:' started at line 1178 (approx).

# Re-assemble the lines
final_lines = lines[:start_idx] + new_content + lines[end_idx:]

with open(file_path, 'w', encoding='utf-8') as f:
    f.writelines(final_lines)

print("Repair successful.")
