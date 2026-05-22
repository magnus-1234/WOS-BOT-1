import os
from PIL import Image, ImageDraw, ImageFont
import io

def test_image_generation():
    print("Starting image generation test...")
    
    # Mock data
    width, height = 1000, 300
    
    # Create base image
    img = Image.new('RGB', (width, height), (100, 100, 255))
    draw = ImageDraw.Draw(img)
    
    # Font loading logic from the fix
    try:
        # We are writing this file to the root of the workspace for execution
        # The font is in DISCORD BOT\fonts\unifont-16.0.04.otf
        
        base_dir = os.getcwd()
        font_path = os.path.join(base_dir, 'DISCORD BOT', 'fonts', 'unifont-16.0.04.otf')
        
        print(f"Looking for font at: {font_path}")
        if os.path.exists(font_path):
            print("Font file found!")
        else:
            print("Font file NOT found!")
            
        font_large = ImageFont.truetype(font_path, 60)
        font_medium = ImageFont.truetype(font_path, 45)
        font_small = ImageFont.truetype(font_path, 35)
        print("Font loaded successfully!")
        
        # Draw text
        text_x = 280
        current_y = 60
        
        draw.text((text_x, current_y), "Welcome TestUser", fill=(255, 255, 255), font=font_medium)
        current_y += 50
        draw.text((text_x, current_y), "to TestServer", fill=(255, 255, 255), font=font_large)
        current_y += 70
        draw.text((text_x, current_y), "you are the 100th member!", fill=(255, 255, 255), font=font_small)
        
        # Save
        img.save("test_welcome.png")
        print("Image saved to test_welcome.png")
        
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    test_image_generation()
