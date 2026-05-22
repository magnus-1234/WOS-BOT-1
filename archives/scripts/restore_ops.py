import sys
import re

def restore_footers(current_path, clean_path, output_path):
    with open(current_path, 'r', encoding='utf-8') as f:
        current_lines = f.readlines()
    with open(clean_path, 'r', encoding='utf-8') as f:
        clean_lines = f.readlines()

    # Find total lines
    # Note: bot_operations.py gained/lost some lines due to the injection (compacting multiline to single line)
    # We will use fuzzy matching or just look at the code structure.
    
    # Actually, the clean source is older. 
    # Let's try to match by surrounding context.
    
    new_lines = []
    i = 0
    while i < len(current_lines):
        line = current_lines[i]
        if 'Whiteout Survival | Magnus' in line:
            # We found an injection. 
            # Look at the previous 3 lines and next 3 lines to find this location in the clean file
            prev_context = "".join(current_lines[i-3:i]).strip()
            next_context = "".join(current_lines[i+1:i+4]).strip()
            
            # Find in clean lines
            found = False
            for j in range(len(clean_lines)):
                clean_prev = "".join(clean_lines[j-3:j]).strip()
                # Use a simpler match (ignoring whitespace differences slightly)
                if prev_context.replace(' ', '') == clean_prev.replace(' ', ''):
                    # Potential match. Check next context
                    # Some footers were multiline in old source
                    # Search ahead in clean lines for the footer end
                    k = j
                    while k < len(clean_lines) and 'set_footer' not in clean_lines[k]:
                        k += 1
                    
                    if k < len(clean_lines) and 'set_footer' in clean_lines[k]:
                        # Extract the whole set_footer block
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
                        
                        # Verify next context after footer block
                        post_footer_context = "".join(clean_lines[m+1:m+4]).strip()
                        if next_context.replace(' ', '')[:20] == post_footer_context.replace(' ', '')[:20]:
                            new_lines.extend(footer_block)
                            found = True
                            print(f"Restored footer at line {i+1} using clean block at {k+1}")
                            break
            
            if not found:
                print(f"Warning: Could not find clean context for footer at line {i+1}")
                new_lines.append(line)
        else:
            new_lines.append(line)
        i += 1

    with open(output_path, 'w', encoding='utf-8') as f:
        f.writelines(new_lines)

if __name__ == "__main__":
    restore_footers(
        'cogs/bot_operations.py',
        'tmp_restore/old_bot_operations_clean.py',
        'cogs/bot_operations_restored.py'
    )
