import asyncio
import json
import logging
import urllib.parse
import requests
import aiohttp
from config import MAPBOX_TOKEN


logger = logging.getLogger(__name__)

# --- ГЕНЕРАТОР КАРТИ (МАРШРУТ ЧЕРЕЗ MAPBOX) ---
def generate_route_image_sync(start_lat, start_lon, end_lat, end_lon, filename="map_preview.png"):
    """
    Генерує преміальну міні-карту через Mapbox Static API.
    Малює маршрут між закладом та клієнтом.
    """
    if not MAPBOX_TOKEN:
        logger.warning("MAPBOX_TOKEN is not set. Map generation is disabled.")
        return None
    try:
        route_url = f"http://router.project-osrm.org/route/v1/driving/{start_lon},{start_lat};{end_lon},{end_lat}?overview=full&geometries=geojson"
        headers = {'User-Agent': 'DeliveProBot/1.0'}
        r = requests.get(route_url, headers=headers, timeout=15)
        
        if r.status_code != 200: 
            logger.error(f"OSRM помилка: {r.status_code}")
            return None
            
        route_data = r.json()
        if not route_data.get('routes'):
            return None
            
        # 2. Формуємо GeoJSON лінію для Mapbox
        coordinates = route_data['routes'][0]['geometry']['coordinates']
        
        # Оптимізація: якщо точок забагато, Mapbox API відхилить запит через довжину URL
        if len(coordinates) > 100:
            coordinates = coordinates[::3]
            
        geojson_line = {
            "type": "Feature",
            "properties": {
                "stroke": "#ff6b4a", 
                "stroke-width": 4,   
                "stroke-opacity": 0.8
            },
            "geometry": {
                "type": "LineString",
                "coordinates": coordinates
            }
        }
        
        geojson_str = json.dumps(geojson_line)
        encoded_geojson = urllib.parse.quote(geojson_str)
        
        # 3. Додаємо маркери 
        marker_biz = f"pin-s+111418({start_lon},{start_lat})"
        marker_client = f"pin-s+3b82f6({end_lon},{end_lat})"
        
        # 4. Збираємо фінальний URL для Mapbox Static Images
        style_id = "streets-v12" 
        width, height = 800, 400
        
        static_url = (
            f"https://api.mapbox.com/styles/v1/mapbox/{style_id}/static/"
            f"geojson({encoded_geojson}),{marker_biz},{marker_client}/"
            f"auto/{width}x{height}@2x?padding=50&access_token={MAPBOX_TOKEN}"
        )
        
        # 5. Завантажуємо картинку
        img_resp = requests.get(static_url, timeout=15)
        if img_resp.status_code == 200:
            with open(filename, 'wb') as f:
                f.write(img_resp.content)
            return filename
        else:
            logger.error(f"Mapbox помилка: {img_resp.text}")
            return None
            
    except Exception as e:
        logger.error(f"Помилка рендеру карти Mapbox: {e}")
        return None


async def get_route_map_file(biz: dict, client_address: str, order_id: str):
    c_lat, c_lon = None, None
    encoded_client = urllib.parse.quote(client_address)
    client_url = f"https://nominatim.openstreetmap.org/search?q={encoded_client}&format=json&limit=1"
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(client_url, headers={'User-Agent': 'DeliveProBot/1.0'}) as resp:
                if resp.status == 200:
                    c_data = await resp.json()
                    if c_data and len(c_data) > 0:
                        c_lat, c_lon = float(c_data[0]['lat']), float(c_data[0]['lon'])
    except Exception as e:
        logger.error(f"❌ Критична помилка Nominatim: {e}")

    if not c_lat: return None 

    biz_address = biz.get('street') if biz else None
    b_lat, b_lon = 50.04132, 21.99901 
    
    if biz_address:
        encoded_biz = urllib.parse.quote(biz_address)
        biz_url = f"https://nominatim.openstreetmap.org/search?q={encoded_biz}&format=json&limit=1"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(biz_url, headers={'User-Agent': 'DeliveProBot/1.0'}) as resp:
                    if resp.status == 200:
                        b_data = await resp.json()
                        if b_data and len(b_data) > 0:
                            b_lat, b_lon = float(b_data[0]['lat']), float(b_data[0]['lon'])
        except Exception as e:
            logger.error(f"Помилка геокодування адреси бізнесу: {e}")

    filename = f"map_{order_id}.png"
    result_file = await asyncio.to_thread(generate_route_image_sync, b_lat, b_lon, c_lat, c_lon, filename)
    return result_file
