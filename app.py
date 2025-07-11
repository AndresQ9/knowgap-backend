import logging
import asyncio
import os
from quart import Quart, request
from quart_cors import cors
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv
from utils.encryption_utils import decrypt_token

from services.course_service import update_student_quiz_data, update_quiz_questions_per_course
from services.video_service import update_course_videos

from routes.base_routes import init_base_routes
from routes.user_routes import init_user_routes
from routes.video_routes import init_video_routes
from routes.support_routes import init_support_routes
from routes.course_routes import init_course_routes
from routes.auth_routes import auth_bp
from routes.canvas_routes import canvas_bp
from routes.skill_routes import skill_bp
from routes.badge_routes import badge_bp
from routes.progress_routes import progress_bp
from routes.analytics_routes import analytics_bp
from routes.achieveup_routes import achieveup_bp

from config import Config

# Set up logging
load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize Quart app
app = Quart(__name__)

# Configure CORS
app = cors(app, 
    allow_origin=[
        "chrome-extension://*",  # Allow all Chrome extensions
        "https://canvas.instructure.com",  # Allow Canvas
        "https://webcourses.ucf.edu",  # Allow UCF Canvas
        "http://localhost:3000",  # Allow AchieveUp frontend development
        "https://achieveup.netlify.app"  # Allow AchieveUp frontend production
    ],
    allow_headers=["Content-Type", "Authorization", "Accept", "Origin", "X-Requested-With"],
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_credentials=True,
    max_age=3600
)

# Custom CORS middleware for additional headers
@app.after_request
async def after_request(response):
    # Add additional CORS headers
    response.headers.add('Access-Control-Allow-Origin', request.headers.get('Origin', '*'))
    response.headers.add('Access-Control-Allow-Headers', 'Content-Type,Authorization,Accept,Origin,X-Requested-With')
    response.headers.add('Access-Control-Allow-Methods', 'GET,POST,PUT,DELETE,OPTIONS')
    response.headers.add('Access-Control-Allow-Credentials', 'true')
    response.headers.add('Access-Control-Max-Age', '3600')
    return response

# Log incoming requests
@app.before_request
async def log_request():
    print(f"Request headers: {dict(request.headers)}")
    print(f"Request method: {request.method}")
    print(f"Request path: {request.path}")

# Initialize routes first
init_base_routes(app)
init_user_routes(app)
init_course_routes(app)
init_video_routes(app)
init_support_routes(app)

# Register AchieveUp blueprints
app.register_blueprint(auth_bp)
app.register_blueprint(canvas_bp)
app.register_blueprint(achieveup_bp)
app.register_blueprint(skill_bp)
app.register_blueprint(badge_bp)
app.register_blueprint(progress_bp)
app.register_blueprint(analytics_bp)

# Apply CORS after routes are initialized

# MongoDB setup
HEX_ENCRYPTION_KEY = Config.HEX_ENCRYPTION_KEY
encryption_key = bytes.fromhex(HEX_ENCRYPTION_KEY)

# Configure MongoDB client
client = AsyncIOMotorClient(
    Config.DB_CONNECTION_STRING,
    tls=True,
    tlsAllowInvalidCertificates=True,
    connectTimeoutMS=30000,
    serverSelectionTimeoutMS=30000
)

db = client[Config.DATABASE]
token_collection = db[Config.TOKENS_COLLECTION]
quizzes_collection = db[Config.QUIZZES_COLLECTION]

# Create indexes
async def create_indexes():
    try:
        logger.info("Attempting to create MongoDB indexes...")
        await quizzes_collection.create_index("course_id")
        logger.info("Successfully created MongoDB indexes")
    except Exception as e:
        logger.error(f"Failed to create MongoDB indexes: {str(e)}")
        # Don't raise the exception - allow the app to continue running
        # The indexes will be created when MongoDB is available

async def scheduled_update():
    logger.info("Scheduled update started")
    try:
        # Test MongoDB connection first
        await db.command('ping')
        logger.info("MongoDB connection successful")
        
        async for token in token_collection.find():
            course_ids = token.get('course_ids')
            authkey = Config.DB_CONNECTION_STRING
            access_token = decrypt_token(encryption_key, token.get('auth'))
            link = token.get('link').replace("https://", "").replace("http://", "")
            logger.info("Processing token with link: %s", link)

            if all([course_ids, access_token, authkey, link]):
                for course_id in course_ids:
                    try:
                        await update_student_quiz_data(course_id, access_token, link)
                        await update_quiz_questions_per_course(course_id, access_token, link)
                        await update_course_videos(course_id)
                        logger.info("Processed course ID: %s", course_id)
                    except Exception as course_error:
                        logger.error("Error processing course ID %s: %s", course_id, course_error)
            else:
                logger.warning("Missing data for token processing: %s", token)
    except Exception as e:
        logger.error("Error in scheduled update: %s", str(e))
        logger.error("Full error details:", exc_info=True)

async def schedule_updates():
    while True:
        try:
            await scheduled_update()
        except Exception as e:
            logger.error(f"Error in schedule_updates: {str(e)}")
        await asyncio.sleep(Config.SET_TIMER)

@app.before_serving
async def startup():
    logger.info("Starting application...")
    try:
        # Try to create indexes, but don't fail if it doesn't work
        await create_indexes()
    except Exception as e:
        logger.error(f"Error during startup: {str(e)}")
    
    # Start the update loop in the background
    asyncio.create_task(schedule_updates())
    logger.info("Application startup complete")

if __name__ == "__main__":
    app.run(debug=True, use_reloader=False)
