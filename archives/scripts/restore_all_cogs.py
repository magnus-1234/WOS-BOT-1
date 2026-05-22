import sys
import os

def restore_footers(current_path, clean_path):
    if not os.path.exists(current_path) or not os.path.exists(clean_path):
        print(f"Error: Path does not exist. Current: {current_path}, Clean: {clean_path}")
        return

    with open(current_path, 'r', encoding='utf-8') as f:
        current_lines = f.readlines()
    with open(clean_path, 'r', encoding='utf-8') as f:
        clean_lines = f.readlines()

    new_lines = []
    i = 0
    restored_count = 0
    warning_count = 0
    
    while i < len(current_lines):
        line = current_lines[i]
        if 'Whiteout Survival | Magnus' in line:
            # Context matching
            prev_context = "".join(current_lines[max(0, i-3):i]).strip()
            next_context = "".join(current_lines[i+1:i+4]).strip()
            
            found = False
            for j in range(len(clean_lines)):
                clean_prev = "".join(clean_lines[max(0, j-3):j]).strip()
                if prev_context.replace(' ', '') == clean_prev.replace(' ', ''):
                    # Find footer block in clean source
                    k = j
                    # Look ahead a bit for the set_footer call
                    found_footer = False
                    for offset in range(10): # Look up to 10 lines ahead for the footer call
                        if k + offset < len(clean_lines) and 'set_footer' in clean_lines[k + offset]:
                            k = k + offset
                            found_footer = True
                            break
                    
                    if found_footer:
                        footer_block = []
                        m = k
                        footer_block.append(clean_lines[m])
                        if '(' in clean_lines[m] and ')' not in clean_lines[m]:
                            m += 1
                            while m < len(clean_lines) and ')' not in clean_lines[m]:
                                footer_block.append(clean_lines[m])
                                m += 1
                            if m < len(clean_lines):
                                footer_block.append(clean_lines[m])
                        
                        # Verify next context
                        post_footer_context = "".join(clean_lines[m+1:min(len(clean_lines), m+4)]).strip()
                        if next_context.replace(' ', '')[:15] == post_footer_context.replace(' ', '')[:15]:
                            new_lines.extend(footer_block)
                            found = True
                            restored_count += 1
                            break
            
            if not found:
                print(f"Warning: Could not find clean context for footer at line {i+1} in {current_path}")
                new_lines.append(line)
                warning_count += 1
        else:
            new_lines.append(line)
        i += 1

    temp_path = current_path + ".restored"
    with open(temp_path, 'w', encoding='utf-8') as f:
        f.writelines(new_lines)
    
    print(f"Finished {current_path}: Restored {restored_count}, Warnings {warning_count}")
    if warning_count == 0 and restored_count > 0:
        os.replace(temp_path, current_path)
        print(f"Successfully updated {current_path}")
    elif warning_count > 0:
        print(f"Manual check required for {current_path}. See {temp_path}")

if __name__ == "__main__":
    mappings = [
        ('cogs/alliance.py', 'tmp_restore/old_alliance.py'),
        ('cogs/bear_trap.py', 'tmp_restore/old_bear_trap.py'),
        ('cogs/gift_operations.py', 'tmp_restore/old_gift_operations.py'),
        ('cogs/start_menu.py', 'tmp_restore/old_start_menu.py'),
        ('cogs/fid_commands.py', 'tmp_restore/old_fid_commands.py'),
        ('cogs/shared_views.py', 'tmp_restore/old_shared_views.py'),
        ('cogs/minister_menu.py', 'tmp_restore/old_minister_menu.py'),
        ('cogs/birthday_system.py', 'tmp_restore/old_birthday_system.py'),
        ('cogs/alliance_member_operations.py', 'tmp_restore/old_alliance_member_operations.py'),
    ]
    
    for curr, clean in mappings:
        restore_footers(curr, clean)
