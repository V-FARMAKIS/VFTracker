from flask import Flask, render_template, jsonify, send_from_directory
from flask_cors import CORS
from pytelematics_oasa import OasaTelematics, Stop, Route
import time
import threading
import logging
import os
import json
from datetime import datetime

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("server.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

app = Flask(__name__, static_folder='static', template_folder='templates')
CORS(app)  # Enable CORS for all routes

# Initialize OASA API
try:
    oasa = OasaTelematics()
    logger.info("Successfully initialized OASA Telematics API")
except Exception as e:
    logger.error(f"Failed to initialize OASA Telematics API: {e}")
    oasa = None

# Default location (2ο ΕΠΑΛ ΙΛΙΟΥ)
DEFAULT_LOCATION = {
    'lat': 38.03700447552456,
    'lng': 23.71519323560343,
    'name': '2ο ΕΠΑΛ ΙΛΙΟΥ'
}

# Cache to store data and reduce API calls
cache = {
    'stops': [],
    'buses': [],
    'last_update': 0,
    'stats': {
        'total_updates': 0,
        'successful_updates': 0,
        'failed_updates': 0,
        'uptime': 0
    }
}

# How often to refresh data (in seconds)
CACHE_DURATION = 20
server_start_time = time.time()

def get_stops(lat, lng):
    """Get nearby bus stops"""
    if oasa is None:
        logger.error("OASA API not initialized")
        return []
    
    try:
        # Log the API call
        logger.info(f"Fetching stops near {lat}, {lng}")
        
        # Make the API call
        stops = oasa.get('getClosestStops', lat, lng)
        
        # Log success
        logger.info(f"Found {len(stops)} stops near {lat}, {lng}")
        
        return stops
    except Exception as e:
        logger.error(f"Error fetching stops: {e}")
        return []

def get_bus_data(stops):
    """Get bus arrival information for a list of stops"""
    if oasa is None:
        logger.error("OASA API not initialized")
        return []
        
    all_buses = []
    errors = 0
    
    for stop_info in stops:
        try:
            stop = Stop(str(stop_info['StopID']))
            arrivals = stop.arrivals()
            
            if arrivals is None:
                logger.info(f"No arrivals for stop {stop_info['StopID']} ({stop_info['StopDescr']})")
                continue
                
            # Get route information for each arrival
            for arrival in arrivals:
                try:
                    routes = oasa.get('webRoutesForStop', str(stop.stopcode))
                    
                    for route_info in routes:
                        if route_info['RouteCode'] == arrival['route_code']:
                            # Add time information to route
                            bus_info = route_info.copy()
                            bus_info['time_left'] = arrival['btime2']
                            bus_info['stop_name'] = stop_info['StopDescr']
                            bus_info['stop_id'] = stop_info['StopID']
                            bus_info['route_code'] = arrival['route_code']
                            
                            # Get bus location
                            try:
                                route = Route(str(arrival['route_code']))
                                bus_location = route.bus_location()
                                
                                if bus_location and len(bus_location) > 0:
                                    bus_info['lat'] = float(bus_location[0]['CS_LAT'])
                                    bus_info['lng'] = float(bus_location[0]['CS_LNG'])
                                    bus_info['vehicle_id'] = bus_location[0]['VEH_NO']
                                    bus_info['last_update'] = datetime.now().strftime('%H:%M:%S')
                                    all_buses.append(bus_info)
                            except Exception as e:
                                logger.error(f"Error fetching bus location for route {arrival['route_code']}: {e}")
                                errors += 1
                except Exception as e:
                    logger.error(f"Error processing arrival for stop {stop_info['StopID']}: {e}")
                    errors += 1
        except Exception as e:
            logger.error(f"Error processing stop {stop_info['StopID']}: {e}")
            errors += 1
    
    logger.info(f"Processed {len(all_buses)} buses with {errors} errors")
    return all_buses

def update_cache():
    """Update the cached data"""
    global cache
    
    while True:
        try:
            update_start = time.time()
            logger.info("Updating cache...")
            cache['stats']['total_updates'] += 1
            
            # Get stops near default location
            stops = get_stops(DEFAULT_LOCATION['lat'], DEFAULT_LOCATION['lng'])
            buses = get_bus_data(stops)
            
            # Update cache
            cache['stops'] = stops
            cache['buses'] = buses
            cache['last_update'] = time.time()
            cache['stats']['successful_updates'] += 1
            cache['stats']['uptime'] = int(time.time() - server_start_time)
            
            update_duration = time.time() - update_start
            logger.info(f"Cache updated with {len(stops)} stops and {len(buses)} buses in {update_duration:.2f} seconds")
            
        except Exception as e:
            logger.error(f"Error updating cache: {e}")
            cache['stats']['failed_updates'] += 1
            
        # Sleep for the cache duration
        time.sleep(CACHE_DURATION)
# Add this route to your app.py file to support bus paths

@app.route('/api/routes/<route_code>')
def get_route_details(route_code):
    """API endpoint to get detailed route information"""
    try:
        if oasa is None:
            return jsonify({"error": "OASA API not initialized"}), 500
            
        # Get route details
        route = Route(route_code)
        
        # Get route path/stops
        route_stops = route.get_route_detail()
        
        # Get bus location
        bus_location = route.bus_location()
        
        return jsonify({
            'route_code': route_code,
            'route_stops': route_stops,
            'bus_location': bus_location
        })
    except Exception as e:
        logger.error(f"Error fetching route details for {route_code}: {e}")
        return jsonify({"error": "Failed to retrieve route details"}), 500
@app.route('/')
def index():
    """Render the main page"""
    try:
        return render_template('index.html', 
                              default_location=DEFAULT_LOCATION, 
                              update_interval=CACHE_DURATION * 1000)  # Convert to milliseconds for JS
    except Exception as e:
        logger.error(f"Error rendering index page: {e}")
        return "Error loading the application. Please check server logs.", 500

@app.route('/api/stops')
def get_stops_api():
    """API endpoint to get nearby stops"""
    try:
        return jsonify(cache['stops'])
    except Exception as e:
        logger.error(f"Error in stops API: {e}")
        return jsonify({"error": "Failed to retrieve stops data"}), 500

@app.route('/api/buses')
def get_buses_api():
    """API endpoint to get bus information"""
    try:
        return jsonify({
            'buses': cache['buses'],
            'last_update': cache['last_update']
        })
    except Exception as e:
        logger.error(f"Error in buses API: {e}")
        return jsonify({"error": "Failed to retrieve bus data"}), 500

@app.route('/api/status')
def get_status():
    """API endpoint to check server status"""
    try:
        return jsonify({
            'status': 'online',
            'last_update': cache['last_update'],
            'stops_count': len(cache['stops']),
            'buses_count': len(cache['buses']),
            'stats': cache['stats'],
            'server_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        })
    except Exception as e:
        logger.error(f"Error in status API: {e}")
        return jsonify({"error": "Failed to retrieve status data"}), 500

@app.route('/static/<path:path>')
def serve_static(path):
    """General handler for static files"""
    try:
        return send_from_directory(app.static_folder, path)
    except Exception as e:
        logger.error(f"Error serving static file {path}: {e}")
        return "File not found", 404

@app.route('/static/audio/<path:filename>')
def serve_audio(filename):
    """Serve audio files"""
    try:
        return send_from_directory(os.path.join(app.static_folder, 'audio'), filename)
    except Exception as e:
        logger.error(f"Error serving audio file {filename}: {e}")
        return "Audio file not found", 404

@app.route('/static/images/<path:filename>')
def serve_images(filename):
    """Serve image files"""
    try:
        return send_from_directory(os.path.join(app.static_folder, 'images'), filename)
    except Exception as e:
        logger.error(f"Error serving image file {filename}: {e}")
        return "Image file not found", 404

@app.route('/api/settings', methods=['GET'])
def get_settings():
    """Get application settings"""
    try:
        # Check if settings file exists
        if os.path.exists('settings.json'):
            with open('settings.json', 'r') as f:
                settings = json.load(f)
            return jsonify(settings)
        else:
            # Default settings
            default_settings = {
                'updateInterval': CACHE_DURATION,
                'darkMode': True,
                'soundEnabled': True,
                'alertThreshold': 3,
                'mapStyle': 'standard'
            }
            # Create settings file
            with open('settings.json', 'w') as f:
                json.dump(default_settings, f, indent=4)
            return jsonify(default_settings)
    except Exception as e:
        logger.error(f"Error in settings API: {e}")
        return jsonify({
            'updateInterval': CACHE_DURATION,
            'darkMode': True,
            'soundEnabled': True,
            'alertThreshold': 3,
            'mapStyle': 'standard'
        })

@app.errorhandler(404)
def page_not_found(e):
    logger.warning(f"404 error: {e}")
    return jsonify({'error': 'Resource not found'}), 404

@app.errorhandler(500)
def server_error(e):
    logger.error(f"500 error: {e}")
    return jsonify({'error': 'Internal server error'}), 500

def check_directories():
    """Ensure required directories exist"""
    try:
        # Check for static directory
        if not os.path.exists(app.static_folder):
            os.makedirs(app.static_folder)
            logger.info(f"Created static directory: {app.static_folder}")
        
        # Check for audio directory
        audio_dir = os.path.join(app.static_folder, 'audio')
        if not os.path.exists(audio_dir):
            os.makedirs(audio_dir)
            logger.info(f"Created audio directory: {audio_dir}")
        
        # Check for images directory
        images_dir = os.path.join(app.static_folder, 'images')
        if not os.path.exists(images_dir):
            os.makedirs(images_dir)
            logger.info(f"Created images directory: {images_dir}")
            
        # Check for css directory
        css_dir = os.path.join(app.static_folder, 'css')
        if not os.path.exists(css_dir):
            os.makedirs(css_dir)
            logger.info(f"Created css directory: {css_dir}")
            
        # Check for js directory
        js_dir = os.path.join(app.static_folder, 'js')
        if not os.path.exists(js_dir):
            os.makedirs(js_dir)
            logger.info(f"Created js directory: {js_dir}")
            
        # Check for templates directory
        if not os.path.exists(app.template_folder):
            os.makedirs(app.template_folder)
            logger.info(f"Created templates directory: {app.template_folder}")
    except Exception as e:
        logger.error(f"Error checking/creating directories: {e}")

if __name__ == "__main__":
    # Make sure required directories exist
    check_directories()
    
    # Start the background thread to update cache
    if oasa is not None:
        update_thread = threading.Thread(target=update_cache, daemon=True)
        update_thread.start()
        logger.info(f"Started update thread with interval of {CACHE_DURATION} seconds")
    else:
        logger.critical("OASA API failed to initialize. Cache updates will not run!")
    
    # Run the Flask app
    try:
        logger.info("Starting Flask server on http://0.0.0.0:5000")
        app.run(host='0.0.0.0', port=5000, debug=True)
    except Exception as e:
        logger.critical(f"Failed to start Flask server: {e}")