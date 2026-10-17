import os
from PIL import Image

folder_path = r"C:\Users\rosen\Documents\PVP\CARDS"
target_width = 450
target_height = 550

for filename in os.listdir(folder_path):
    if filename.lower().endswith(('.png', '.jpg', '.jpeg')):
        file_path = os.path.join(folder_path, filename)
        img = Image.open(file_path).convert("RGBA")
        
        # Forzar directamente las dimensiones exactas (450x550) sin mantener proporciones
        resized_img = img.resize((target_width, target_height), Image.Resampling.LANCZOS)
        
        # Guardar como PNG con el tamaño exacto forzado
        base_name, _ = os.path.splitext(file_path)
        output_path = base_name + ".png"
        resized_img.save(output_path, "PNG")
        
        # Limpiar si el original era .jpg antiguo
        if file_path.lower().endswith(('.jpg', '.jpeg')) and output_path != file_path:
            if os.path.exists(file_path):
                os.remove(file_path)

print("¡Todas las cartas han sido forzadas exactamente a 450x550 píxeles!")