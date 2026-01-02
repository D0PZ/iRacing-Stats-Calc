"""
Script para generar el icono de iRacing Stats Calculator
"""
from PIL import Image, ImageDraw, ImageFont
import os

def create_icon():
    # Tamaños para el .ico (múltiples resoluciones)
    sizes = [256, 128, 64, 48, 32, 16]
    
    images = []
    
    for size in sizes:
        # Crear imagen con fondo oscuro
        img = Image.new('RGBA', (size, size), (13, 17, 23, 255))  # Color #0D1117
        draw = ImageDraw.Draw(img)
        
        # Calcular proporciones
        padding = size // 8
        center = size // 2
        
        # Dibujar un círculo de fondo (como velocímetro)
        circle_radius = size // 2 - padding
        draw.ellipse(
            [center - circle_radius, center - circle_radius,
             center + circle_radius, center + circle_radius],
            outline=(88, 166, 255, 255),  # #58A6FF
            width=max(2, size // 32)
        )
        
        # Dibujar arco de "velocímetro" (parte superior)
        arc_padding = padding + size // 16
        arc_radius = circle_radius - size // 16
        draw.arc(
            [center - arc_radius, center - arc_radius,
             center + arc_radius, center + arc_radius],
            start=220, end=320,
            fill=(63, 185, 80, 255),  # Verde #3FB950
            width=max(3, size // 20)
        )
        
        # Dibujar líneas de gráfico/estadísticas
        if size >= 32:
            # Línea de tendencia ascendente
            line_start_x = center - size // 4
            line_start_y = center + size // 6
            line_end_x = center + size // 4
            line_end_y = center - size // 6
            
            draw.line(
                [(line_start_x, line_start_y), (line_end_x, line_end_y)],
                fill=(139, 92, 246, 255),  # Púrpura #8B5CF6
                width=max(2, size // 40)
            )
            
            # Puntos en la línea
            point_radius = max(2, size // 40)
            for i in range(3):
                px = line_start_x + (line_end_x - line_start_x) * i // 2
                py = line_start_y + (line_end_y - line_start_y) * i // 2
                draw.ellipse(
                    [px - point_radius, py - point_radius,
                     px + point_radius, py + point_radius],
                    fill=(88, 166, 255, 255)  # #58A6FF
                )
        
        # Dibujar "iR" en el centro si hay espacio
        if size >= 48:
            try:
                # Intentar usar una fuente del sistema
                font_size = size // 4
                try:
                    font = ImageFont.truetype("segoeui.ttf", font_size)
                except:
                    try:
                        font = ImageFont.truetype("arial.ttf", font_size)
                    except:
                        font = ImageFont.load_default()
                
                text = "iR"
                # Obtener tamaño del texto
                bbox = draw.textbbox((0, 0), text, font=font)
                text_width = bbox[2] - bbox[0]
                text_height = bbox[3] - bbox[1]
                
                text_x = center - text_width // 2
                text_y = center - text_height // 2 + size // 8
                
                draw.text((text_x, text_y), text, 
                         fill=(255, 255, 255, 255), font=font)
            except:
                pass
        
        images.append(img)
    
    # Guardar como .ico con múltiples tamaños
    icon_path = os.path.join(os.path.dirname(__file__), 'icon.ico')
    
    # El primer tamaño es el principal
    images[0].save(
        icon_path,
        format='ICO',
        sizes=[(s, s) for s in sizes],
        append_images=images[1:]
    )
    
    print(f"✅ Icono creado: {icon_path}")
    return icon_path

if __name__ == "__main__":
    create_icon()
