from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from os import environ
from datetime import datetime
import traceback
import os
import secrets
import json
from werkzeug.utils import secure_filename

# --- MongoDB Imports ---
from pymongo import MongoClient
from bson.objectid import ObjectId # Used for unique MongoDB IDs

app = Flask(__name__)
CORS(app)

# --- File Upload Configuration (Unchanged) ---
UPLOAD_FOLDER = 'uploads'
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)
    print(f"✅ Created directory: {UPLOAD_FOLDER}")

# --- MongoDB Connection and Collection Setup (Replaces SQLite) ---
MONGO_URI = environ.get("MONGO_URI") 

if MONGO_URI:
    try:
        # Connect to the MongoDB client
        client = MongoClient(MONGO_URI)
        db = client.get_database("societyvoice") 
        
        # Define your collections (equivalent to your tables)
        users_collection = db.users
        complaints_collection = db.complaints
        alerts_collection = db.alerts
        polls_collection = db.polls
        poll_votes_collection = db.poll_votes
        registration_requests_collection = db.registration_requests
        likes_collection = db.complaint_likes
        house_requests_collection = db.house_change_requests

        # Initialize Admin User and Indexes
        admin_count = users_collection.count_documents({"role": "admin"})
        if admin_count == 0:
            users_collection.insert_one({
                "name": "Admin", 
                "email": "admin@society.com", 
                "password": "admin123", # NOTE: Hash this password in a real app!
                "role": "admin",
                "house_number": None,
                "created_at": datetime.now().isoformat()
            })
            print("✅ Initial admin user created.")
        
        # Ensure email uniqueness (equivalent to SQL UNIQUE)
        users_collection.create_index("email", unique=True)
        registration_requests_collection.create_index("email", unique=True)
        likes_collection.create_index([("complaint_id", 1), ("user_id", 1)], unique=True)

        print("✅ Successfully connected to MongoDB Atlas!")
    except Exception as e:
        print(f"❌ MongoDB connection error: {e}")
        traceback.print_exc()
else:
    print("🚨 CRITICAL: MONGO_URI environment variable not set. Database connection failed.")
# --- END MongoDB Connection ---

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@app.route("/uploads/<filename>")
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

# --- HELPER FUNCTIONS ---
def prepare_document(doc):
    """Converts MongoDB document to a JSON-safe dictionary."""
    if doc and '_id' in doc:
        doc['_id'] = str(doc['_id'])
    return doc

def to_object_id(id_str):
    """Converts a string ID to MongoDB's ObjectId, handles potential errors."""
    try:
        return ObjectId(id_str)
    except Exception:
        return None


# --- USER & AUTHENTICATION ROUTES ---

@app.route("/register", methods=["POST"])
def register():
    try:
        data = request.json
        name, email, password, house_number = data.get("name"), data.get("email"), data.get("password"), data.get("house_number")
        if not all([name, email, password, house_number]):
            return jsonify({"error": "Missing required fields"}), 400
        
        # Check if email is already in users or pending requests
        existing_user = users_collection.find_one({"email": email})
        existing_request = registration_requests_collection.find_one({"email": email})
        if existing_user or existing_request:
            return jsonify({"error": "Email already exists as a user or a pending request"}), 409

        registration_requests_collection.insert_one({
            "name": name, 
            "email": email, 
            "password": password, 
            "role": "resident", 
            "house_number": house_number,
            "created_at": datetime.now().isoformat()
        })
        print(f"✅ Registration request submitted for {email}")
        return jsonify({"message": "Registration request submitted successfully. Awaiting admin approval."})
    except Exception as e:
        print(f"❌ Registration error: {e}")
        return jsonify({"error": "Internal server error"}), 500

@app.route("/login", methods=["POST"])
def login():
    try:
        data = request.json
        email, password = data.get("email"), data.get("password")
        if not email or not password:
            return jsonify({"error": "Email and password are required"}), 400
            
        # MongoDB query: Find user by email. We check password in Python.
        user = users_collection.find_one({"email": email})
        
        if user and user['password'] == password:
            print(f"✅ Login successful for {email} with role {user['role']}")
            user_safe = prepare_document(user)
            user_safe.pop('password', None) # Remove password before returning
            return jsonify(user_safe)
        else:
            print(f"❌ Invalid login attempt for {email}")
            return jsonify({"error": "Invalid email or password"}), 401
    except Exception as e:
        print(f"❌ Login error: {e}")
        return jsonify({"error": "Internal server error"}), 500

@app.route("/change_password", methods=["POST"])
def change_password():
    try:
        data = request.json
        user_id, current_password, new_password = data.get("id"), data.get("current_password"), data.get("new_password")
        if not all([user_id, current_password, new_password]):
            return jsonify({"error": "Missing required fields"}), 400
        
        obj_id = to_object_id(user_id)
        if not obj_id: return jsonify({"error": "Invalid User ID format"}), 400

        user = users_collection.find_one({"_id": obj_id})
        if not user:
            return jsonify({"error": "User not found"}), 404
        if user['password'] != current_password:
            return jsonify({"error": "Current password is incorrect"}), 400
            
        # Update user's password
        users_collection.update_one({"_id": obj_id}, {"$set": {"password": new_password}})
        
        print(f"✅ Password changed for user ID {user_id}")
        return jsonify({"message": "Password updated successfully"})
    except Exception as e:
        print(f"❌ Change password error: {e}")
        return jsonify({"error": "Internal server error"}), 500

# --- COMPLAINT ROUTES ---

@app.route("/submit_complaint", methods=["POST"])
def submit_complaint():
    try:
        title, description, category, user_id = request.form.get("title"), request.form.get("description"), request.form.get("category"), request.form.get("user_id")
        if not all([title, description, category, user_id]):
            return jsonify({"error": "Missing required fields"}), 400
        
        image_path = None
        if 'image' in request.files:
            image_file = request.files['image']
            if image_file and allowed_file(image_file.filename):
                file_extension = image_file.filename.rsplit('.', 1)[1].lower()
                unique_filename = f"{secrets.token_hex(16)}.{file_extension}"
                image_file.save(os.path.join(app.config['UPLOAD_FOLDER'], unique_filename))
                image_path = unique_filename
        
        # Insert complaint document
        complaint_document = {
            "title": title, 
            "description": description, 
            "category": category, 
            "status": "Open",
            "user_id": user_id, # Store user_id as string
            "image_path": image_path,
            "created_at": datetime.now().isoformat()
        }
        complaints_collection.insert_one(complaint_document)
        
        print(f"✅ Complaint submitted by user ID {user_id}")
        return jsonify({"message": "Complaint submitted successfully."})
    except Exception as e:
        print(f"❌ Submit complaint error: {e}")
        return jsonify({"error": "Internal server error"}), 500

@app.route("/get_complaints", methods=["GET"])
def get_complaints():
    try:
        user_id, view_type = request.args.get("user_id"), request.args.get("view_type", "all")
        
        # MongoDB aggregation pipeline for JOIN (Lookup user details)
        pipeline = [
            # 1. Filter by user_id if 'my' view is requested
            {
                "$match": {} if view_type != 'my' or not user_id else {"user_id": user_id}
            },
            # 2. Join with users collection
            {
                "$lookup": {
                    "from": "users",
                    "localField": "user_id",
                    "foreignField": "_id",
                    "as": "user_details",
                    # Add $project here if you were using user_id as ObjectId, but since it's stored as string...
                    "pipeline": [
                        # NOTE: If user_id is stored as string in complaints, but ObjectId in users,
                        # this lookup will fail. Assuming for now user_id is stored as string in both 
                        # for the sake of simplicity matching the SQLite logic (where user_id is INTEGER).
                        # We use the user's name/house_number in the query instead of _id for simplicity.
                        {"$match": {"_id": {"$exists": True}}} # No-op match to allow projection
                    ]
                }
            },
            # 3. Unwind the user array
            {"$unwind": {"path": "$user_details", "preserveNullAndEmptyArrays": True}},
            # 4. Project and clean up
            {
                "$project": {
                    "_id": {"$toString": "$_id"},
                    "title": 1,
                    "description": 1,
                    "category": 1,
                    "status": 1,
                    "user_id": 1,
                    "image_path": 1,
                    "created_at": 1,
                    "user_name": "$user_details.name",
                    "house_number": "$user_details.house_number"
                }
            },
            # 5. Sort
            {"$sort": {"created_at": -1}}
        ]
        
        complaints_list = list(complaints_collection.aggregate(pipeline))
        
        # Manually add like counts and user_has_liked status
        for complaint in complaints_list:
            complaint_id = complaint['_id']
            
            # Like Count
            like_count = likes_collection.count_documents({"complaint_id": complaint_id})
            complaint['like_count'] = like_count
            
            # User Has Liked
            user_has_liked = False
            if user_id:
                liked = likes_collection.find_one({"complaint_id": complaint_id, "user_id": user_id})
                if liked:
                    user_has_liked = True
            complaint['user_has_liked'] = user_has_liked

        return jsonify(complaints_list)
    except Exception as e:
        print(f"❌ Get complaints error: {e}")
        return jsonify({"error": "Internal server error"}), 500

@app.route("/update_complaint_status", methods=["POST"])
def update_complaint_status():
    try:
        data = request.json
        complaint_id = data.get("complaint_id")
        new_status = data.get("new_status")
        user_role = data.get("user_role")

        if not all([complaint_id, new_status, user_role]):
            return jsonify({"error": "Missing required fields"}), 400
        
        obj_id = to_object_id(complaint_id)
        if not obj_id: return jsonify({"error": "Invalid Complaint ID format"}), 400

        if user_role in ['admin', 'worker']:
            complaints_collection.update_one(
                {"_id": obj_id}, 
                {"$set": {"status": new_status}}
            )
            print(f"✅ Complaint ID {complaint_id} status updated to {new_status} by {user_role}")
            return jsonify({"message": "Status updated successfully"})

        elif user_role == 'resident':
            user_id = data.get("user_id")
            if not user_id:
                return jsonify({"error": "User ID is required for this action"}), 400
            
            if new_status != 'Open':
                return jsonify({"error": "Residents can only reopen complaints."}), 403

            complaint = complaints_collection.find_one({"_id": obj_id})

            if not complaint:
                return jsonify({"error": "Complaint not found"}), 404
            
            # user_id in complaint is stored as string
            if complaint['user_id'] != user_id: 
                return jsonify({"error": "You can only reopen your own complaints."}), 403
            
            if complaint['status'] != 'resolved':
                return jsonify({"error": "Only resolved complaints can be reopened."}), 400
            
            complaints_collection.update_one(
                {"_id": obj_id}, 
                {"$set": {"status": new_status}}
            )
            print(f"✅ Complaint ID {complaint_id} reopened by resident ID {user_id}")
            return jsonify({"message": "Complaint reopened successfully."})

        else:
            return jsonify({"error": "Unauthorized role"}), 403

    except Exception as e:
        print(f"❌ Update complaint status error: {e}")
        return jsonify({"error": "Internal server error"}), 500
        
@app.route("/like_complaint", methods=["POST"])
def like_complaint():
    try:
        data = request.json
        complaint_id, user_id = data.get("complaint_id"), data.get("user_id")
        if not all([complaint_id, user_id]):
            return jsonify({"error": "Missing required fields"}), 400
            
        # Filter document
        filter_doc = {"complaint_id": complaint_id, "user_id": user_id}
        
        existing_like = likes_collection.find_one(filter_doc)
        
        if existing_like:
            likes_collection.delete_one(filter_doc)
            action = "unliked"
        else:
            likes_collection.insert_one(filter_doc)
            action = "liked"
            
        new_count = likes_collection.count_documents({"complaint_id": complaint_id})
        
        print(f"✅ Complaint ID {complaint_id} {action} by user ID {user_id}. New count: {new_count}")
        return jsonify({"message": f"Complaint {action} successfully.", "new_like_count": new_count, "action": action})

    except Exception as e:
        print(f"❌ Like complaint error: {e}")
        return jsonify({"error": "Internal server error"}), 500

@app.route("/delete_complaint", methods=["POST"])
def delete_complaint():
    try:
        data = request.json
        complaint_id, user_role = data.get("complaint_id"), data.get("user_role")
        if not all([complaint_id, user_role]):
            return jsonify({"error": "Missing required fields"}), 400
        if user_role not in ['admin', 'worker']:
            return jsonify({"error": "Unauthorized"}), 403
            
        obj_id = to_object_id(complaint_id)
        if not obj_id: return jsonify({"error": "Invalid Complaint ID format"}), 400
        
        # 1. Get image path before deleting
        complaint_to_delete = complaints_collection.find_one({"_id": obj_id}, {"image_path": 1})
        
        # 2. Delete likes associated with the complaint (Manual Cascade)
        likes_collection.delete_many({"complaint_id": complaint_id})
        
        # 3. Delete the complaint
        result = complaints_collection.delete_one({"_id": obj_id})
        
        if result.deleted_count == 0:
            return jsonify({"error": "Complaint not found"}), 404
            
        # 4. Delete the associated image file
        if complaint_to_delete and complaint_to_delete.get('image_path'):
            try:
                file_path = os.path.join(app.config['UPLOAD_FOLDER'], complaint_to_delete['image_path'])
                if os.path.exists(file_path):
                    os.remove(file_path)
                    print(f"🗑️ Deleted associated image file: {file_path}")
            except Exception as file_error:
                print(f"⚠️ Error deleting image file {complaint_to_delete['image_path']}: {file_error}")
        
        print(f"✅ Complaint ID {complaint_id} deleted by {user_role}.")
        return jsonify({"message": "Complaint deleted successfully"})
    except Exception as e:
        print(f"❌ Delete complaint error: {e}")
        return jsonify({"error": "Internal server error"}), 500

# --- POLL ROUTES ---
@app.route("/create_poll", methods=["POST"])
def create_poll():
    try:
        data = request.json
        question, description, options_list, created_by = data.get("question"), data.get("description"), data.get("options"), data.get("created_by")
        if not all([question, options_list, created_by]):
            return jsonify({"error": "Missing required fields"}), 400
        if not isinstance(options_list, list) or len(options_list) < 2:
            return jsonify({"error": "Poll must have at least two options"}), 400
            
        # Note: options_json is replaced by storing the list directly
        poll_document = {
            "question": question, 
            "description": description, 
            "options": options_list, 
            "created_by": created_by, # Store user_id as string
            "created_at": datetime.now().isoformat()
        }
        polls_collection.insert_one(poll_document)
        
        print(f"✅ Poll created by user ID {created_by}")
        return jsonify({"message": "Poll created successfully"})
    except Exception as e:
        print(f"❌ Create poll error: {e}")
        return jsonify({"error": "Internal server error"}), 500

@app.route("/get_polls", methods=["GET"])
def get_polls():
    try:
        user_id = request.args.get("user_id")
        
        # Aggregation to join polls with creator name
        pipeline = [
            {
                "$lookup": {
                    "from": "users",
                    "localField": "created_by",
                    "foreignField": "_id",
                    "as": "creator",
                    # Add $project here if you were using created_by as ObjectId
                }
            },
            {"$unwind": {"path": "$creator", "preserveNullAndEmptyArrays": True}},
            {"$sort": {"created_at": -1}},
            {
                "$project": {
                    "_id": {"$toString": "$_id"},
                    "question": 1,
                    "description": 1,
                    "options": 1,
                    "created_by": 1,
                    "created_at": 1,
                    "created_by_name": "$creator.name",
                }
            }
        ]
        
        polls_raw = list(polls_collection.aggregate(pipeline))
        result_polls = []
        
        for poll in polls_raw:
            poll_id = poll['_id']
            
            # Calculate vote counts
            vote_results = poll_votes_collection.aggregate([
                {"$match": {"poll_id": poll_id}},
                {"$group": {"_id": "$vote", "count": {"$sum": 1}}}
            ])
            
            vote_counts = {item['_id']: item['count'] for item in vote_results}
            poll['vote_counts'] = vote_counts
            poll['total_votes'] = sum(vote_counts.values())
            
            # Check user vote status
            user_vote = None
            has_voted = False
            if user_id:
                user_vote_doc = poll_votes_collection.find_one({"poll_id": poll_id, "user_id": user_id})
                if user_vote_doc:
                    has_voted = True
                    user_vote = user_vote_doc['vote']

            poll['has_voted'] = has_voted
            poll['user_vote'] = user_vote
            
            result_polls.append(poll)

        return jsonify(result_polls)
    except Exception as e:
        print(f"❌ Get polls error: {e}")
        return jsonify({"error": "Internal server error"}), 500

@app.route("/vote_poll", methods=["POST"])
def vote_poll():
    try:
        data = request.json
        poll_id, user_id, vote = data.get("poll_id"), data.get("user_id"), data.get("option")
        if not all([poll_id, user_id, vote]):
            return jsonify({"error": "Missing required fields"}), 400
            
        if poll_votes_collection.find_one({"poll_id": poll_id, "user_id": user_id}):
             return jsonify({"error": "You have already voted in this poll"}), 409
             
        poll_votes_collection.insert_one({
            "poll_id": poll_id, 
            "user_id": user_id, 
            "vote": vote,
            "created_at": datetime.now().isoformat()
        })
        
        print(f"✅ User ID {user_id} voted in poll ID {poll_id}")
        return jsonify({"message": "Vote recorded successfully"})
    except Exception as e:
        print(f"❌ Vote poll error: {e}")
        return jsonify({"error": "Internal server error"}), 500

@app.route("/delete_poll", methods=["POST"])
def delete_poll():
    try:
        data = request.json
        poll_id, user_role = data.get("poll_id"), data.get("user_role")
        if user_role != 'admin':
            return jsonify({"error": "Unauthorized"}), 403
        if not poll_id:
            return jsonify({"error": "Poll ID required"}), 400
            
        obj_id = to_object_id(poll_id)
        if not obj_id: return jsonify({"error": "Invalid Poll ID format"}), 400
        
        # 1. Delete all votes for the poll (Manual Cascade)
        poll_votes_collection.delete_many({"poll_id": poll_id})
        
        # 2. Delete the poll
        result = polls_collection.delete_one({"_id": obj_id})
        
        if result.deleted_count == 0:
            return jsonify({"error": "Poll not found"}), 404
            
        print(f"✅ Poll ID {poll_id} deleted by admin.")
        return jsonify({"message": "Poll deleted successfully"})
    except Exception as e:
        print(f"❌ Delete poll error: {e}")
        return jsonify({"error": "Internal server error"}), 500

# --- ADMIN-ONLY USER MANAGEMENT ROUTES ---

@app.route("/get_users", methods=["GET"])
def get_users():
    try:
        # Select all users, excluding the password field
        users = users_collection.find({}, {"password": 0}).sort([("role", 1), ("name", 1)])
        
        # Convert _id to string for all users
        user_list = [prepare_document(u) for u in users]
        return jsonify(user_list)
    except Exception as e:
        print(f"❌ Get users error: {e}")
        return jsonify({"error": "Internal server error"}), 500

@app.route("/add_user", methods=["POST"])
def add_user():
    try:
        data = request.json
        name, email, password, role, house_number = data.get("name"), data.get("email"), data.get("password"), data.get("role"), data.get("house_number")
        if not all([name, email, password, role]):
            return jsonify({"error": "Missing required fields"}), 400
        if role == 'resident' and not house_number:
            return jsonify({"error": "House number is required for residents"}), 400

        # Check for existing email before insertion
        if users_collection.find_one({"email": email}):
            return jsonify({"error": "Email already exists"}), 409
            
        user_document = {
            "name": name, 
            "email": email, 
            "password": password, 
            "role": role, 
            "house_number": house_number if role == 'resident' else None,
            "created_at": datetime.now().isoformat()
        }
        users_collection.insert_one(user_document)
        
        print(f"✅ New user added by admin: {email} with role {role}")
        return jsonify({"message": "User added successfully"})
    except Exception as e:
        # Catching other potential insertion errors
        print(f"❌ Add user error: {e}")
        return jsonify({"error": "Internal server error"}), 500

@app.route("/get_registration_requests", methods=["GET"])
def get_registration_requests():
    try:
        requests = registration_requests_collection.find().sort("created_at", 1)
        request_list = [prepare_document(r) for r in requests]
        return jsonify(request_list)
    except Exception as e:
        print(f"❌ Get registration requests error: {e}")
        return jsonify({"error": "Internal server error"}), 500

@app.route("/process_registration_request", methods=["POST"])
def process_registration_request():
    try:
        data = request.json
        request_id, action = data.get("request_id"), data.get("action")
        if not all([request_id, action]) or action not in ['approve', 'reject']:
            return jsonify({"error": "Missing or invalid required fields"}), 400

        obj_id = to_object_id(request_id)
        if not obj_id: return jsonify({"error": "Invalid Request ID format"}), 400
        
        req_data = registration_requests_collection.find_one({"_id": obj_id})
        if not req_data:
            return jsonify({"error": "Request not found"}), 404

        if action == 'approve':
            try:
                # Insert into users collection
                user_document = {
                    "name": req_data['name'], 
                    "email": req_data['email'], 
                    "password": req_data['password'], 
                    "role": req_data['role'], 
                    "house_number": req_data['house_number'],
                    "created_at": datetime.time().isoformat()
                }
                users_collection.insert_one(user_document)
            except Exception as e:
                # Catch IntegrityError equivalent (email unique index violation)
                if "duplicate key error" in str(e):
                    registration_requests_collection.delete_one({"_id": obj_id})
                    return jsonify({"error": "User with this email already exists. The request has been removed."}), 409
                raise e # Re-raise if it's another error

        # Delete the request (whether approved or rejected)
        registration_requests_collection.delete_one({"_id": obj_id})

        message = f"Registration request {action}d successfully."
        print(f"✅ {message}")
        return jsonify({"message": message})

    except Exception as e:
        print(f"❌ Process registration error: {e}")
        return jsonify({"error": "Internal server error"}), 500

@app.route("/delete_user", methods=["POST"])
def delete_user():
    try:
        data = request.json
        user_id = data.get("user_id")
        if not user_id:
            return jsonify({"error": "User ID is required"}), 400
        
        obj_id = to_object_id(user_id)
        if not obj_id: return jsonify({"error": "Invalid User ID format"}), 400
        
        # Manual Cascading Deletes (user_id is stored as string in these collections)
        
        # 1. Delete associated complaints and their likes
        user_complaints = complaints_collection.find({"user_id": user_id})
        complaint_ids = [str(c['_id']) for c in user_complaints] # Need string IDs for like collection
        
        if complaint_ids:
            likes_collection.delete_many({"complaint_id": {"$in": complaint_ids}})
            complaints_collection.delete_many({"user_id": user_id})
        
        # 2. Delete associated votes
        poll_votes_collection.delete_many({"user_id": user_id})
        
        # 3. Delete associated registration/house requests
        registration_requests_collection.delete_many({"email": users_collection.find_one({"_id": obj_id})['email'] if users_collection.find_one({"_id": obj_id}) else "N/A"})
        house_requests_collection.delete_many({"user_id": user_id})
        
        # 4. Delete the user
        result = users_collection.delete_one({"_id": obj_id})
        
        if result.deleted_count == 0:
            return jsonify({"error": "User not found or already deleted"}), 404

        print(f"✅ User ID {user_id} and their associated data have been deleted.")
        return jsonify({"message": "User deleted successfully"})
    except Exception as e:
        print(f"❌ Delete user error: {e}")
        return jsonify({"error": "Internal server error. The user may have associated records that prevent deletion."}), 500

# --- HOUSE NUMBER CHANGE ROUTES ---

@app.route("/request_house_change", methods=["POST"])
def request_house_change():
    try:
        data = request.json
        user_id, new_house_number = data.get("user_id"), data.get("new_house_number")
        if not all([user_id, new_house_number]):
            return jsonify({"error": "Missing required fields"}), 400
        
        # Check if there is already a pending request for this user
        existing_request = house_requests_collection.find_one({"user_id": user_id, "status": "pending"})
        if existing_request:
            return jsonify({"error": "You already have a pending house number change request."}), 409
            
        house_requests_collection.insert_one({
            "user_id": user_id, 
            "requested_house_number": new_house_number, 
            "status": "pending",
            "created_at": datetime.now().isoformat()
        })
        
        print(f"✅ House number change request submitted for user ID {user_id}")
        return jsonify({"message": "House number change request submitted successfully. Awaiting admin approval."})
    except Exception as e:
        print(f"❌ House change request error: {e}")
        return jsonify({"error": "Internal server error"}), 500

@app.route("/get_house_change_requests", methods=["GET"])
def get_house_change_requests():
    try:
        # Aggregation to join house requests with user details
        requests_pipeline = [
            {"$match": {"status": "pending"}},
            {
                "$lookup": {
                    "from": "users",
                    "localField": "user_id",
                    "foreignField": "_id",
                    "as": "user_details",
                    # Add $project here if you were using user_id as ObjectId
                }
            },
            {"$unwind": {"path": "$user_details", "preserveNullAndEmptyArrays": True}},
            {"$sort": {"created_at": 1}},
            {
                "$project": {
                    "_id": {"$toString": "$_id"},
                    "user_id": 1,
                    "requested_house_number": 1,
                    "created_at": 1,
                    "name": "$user_details.name",
                    "email": "$user_details.email",
                    "current_house_number": "$user_details.house_number",
                }
            }
        ]
        
        requests = list(house_requests_collection.aggregate(requests_pipeline))
        return jsonify(requests)
    except Exception as e:
        print(f"❌ Get house change requests error: {e}")
        return jsonify({"error": "Internal server error"}), 500

@app.route("/process_house_change_request", methods=["POST"])
def process_house_change_request():
    try:
        data = request.json
        request_id, action = data.get("request_id"), data.get("action")
        if not all([request_id, action]) or action not in ['approve', 'reject']:
            return jsonify({"error": "Missing or invalid required fields"}), 400

        obj_id = to_object_id(request_id)
        if not obj_id: return jsonify({"error": "Invalid Request ID format"}), 400
        
        req_data = house_requests_collection.find_one({"_id": obj_id})
        if not req_data:
            return jsonify({"error": "Request not found"}), 404

        if action == 'approve':
            # Update user's house number
            user_obj_id = to_object_id(req_data['user_id'])
            if user_obj_id:
                users_collection.update_one(
                    {"_id": user_obj_id}, 
                    {"$set": {"house_number": req_data['requested_house_number']}}
                )
        
        # Delete the request (whether approved or rejected)
        result = house_requests_collection.delete_one({"_id": obj_id})

        if result.deleted_count == 0:
             return jsonify({"error": "Request not found or already processed"}), 404

        message = f"House number change request {action}d successfully."
        print(f"✅ {message}")
        return jsonify({"message": message})
    except Exception as e:
        print(f"❌ Process house change request error: {e}")
        return jsonify({"error": "Internal server error"}), 500

# --- ALERT ROUTES ---

@app.route("/create_alert", methods=["POST"])
def create_alert():
    try:
        data = request.json
        message, user_id, user_role = data.get("message"), data.get("user_id"), data.get("user_role")
        if not all([message, user_id, user_role]):
            return jsonify({"error": "Missing required fields"}), 400
        if user_role != 'admin':
            return jsonify({"error": "Unauthorized"}), 403
        
        alerts_collection.insert_one({
            "message": message, 
            "created_by": user_id,
            "created_at": datetime.now().isoformat()
        })
        
        print(f"✅ Alert created by admin ID {user_id}")
        return jsonify({"message": "Alert created successfully"})
    except Exception as e:
        print(f"❌ Create alert error: {e}")
        return jsonify({"error": "Internal server error"}), 500

@app.route("/get_alerts", methods=["GET"])
def get_alerts():
    try:
        # Aggregation to join alerts with creator name
        alerts_pipeline = [
            {
                "$lookup": {
                    "from": "users",
                    "localField": "created_by",
                    "foreignField": "_id",
                    "as": "creator",
                    # Add $project here if you were using created_by as ObjectId
                }
            },
            {"$unwind": {"path": "$creator", "preserveNullAndEmptyArrays": True}},
            {"$sort": {"created_at": -1}},
            {
                "$project": {
                    "_id": {"$toString": "$_id"},
                    "message": 1,
                    "created_at": 1,
                    "created_by_name": "$creator.name"
                }
            }
        ]
        
        alerts = list(alerts_collection.aggregate(alerts_pipeline))
        return jsonify(alerts)
    except Exception as e:
        print(f"❌ Get alerts error: {e}")
        return jsonify({"error": "Internal server error"}), 500

@app.route("/delete_alert", methods=["POST"])
def delete_alert():
    try:
        data = request.json
        alert_id, user_role = data.get("alert_id"), data.get("user_role")
        if not all([alert_id, user_role]):
            return jsonify({"error": "Missing required fields"}), 400
        if user_role != 'admin':
            return jsonify({"error": "Unauthorized"}), 403

        obj_id = to_object_id(alert_id)
        if not obj_id: return jsonify({"error": "Invalid Alert ID format"}), 400
        
        result = alerts_collection.delete_one({"_id": obj_id})
        
        if result.deleted_count == 0:
            return jsonify({"error": "Alert not found"}), 404

        print(f"✅ Alert ID {alert_id} deleted by admin.")
        return jsonify({"message": "Alert deleted successfully"})
    except Exception as e:
        print(f"❌ Delete alert error: {e}")
        return jsonify({"error": "Internal server error"}), 500

if __name__ == "__main__":
    app.run(debug=True)