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


# --- MongoDB Connection and Collection Setup (CRITICAL UPDATE) ---
MONGO_URI = environ.get("MONGO_URI") 

# Global declarations - collections will be assigned inside the try block
users_collection = None
complaints_collection = None
alerts_collection = None
polls_collection = None
poll_votes_collection = None
registration_requests_collection = None
likes_collection = None
house_requests_collection = None

if MONGO_URI:
    try:
        # Connect to the MongoDB client
        # Added a timeout to prevent indefinite waiting if the URI is wrong/network blocked
        client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000) 
        
        # Test the connection by sending a command. This is where the app will crash if the URI is bad or network is blocked.
        client.admin.command('ping') 
        print("✅ Successfully connected to MongoDB Atlas!") # You must see this in Vercel logs
        
        # If connection succeeds, assign the collections
        db = client.get_database("societyvoice") 
        
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

    except Exception as e:
        # This will print the actual MongoDB error (e.g., Auth failure) to Vercel logs
        print(f"❌ MongoDB Connection FAILED: {e}") 
        traceback.print_exc()
        # CRITICALLY: Force the Vercel function to exit/crash on failure
        exit(1)
else:
    print("🚨 CRITICAL: MONGO_URI environment variable not set. Database connection failed.")
    # CRITICALLY: Force the Vercel function to exit/crash on missing variable
    exit(1)
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
            return jsonify({"error": "Incorrect current password"}), 401

        # NOTE: In a real application, you should hash the password before saving
        users_collection.update_one({"_id": obj_id}, {"$set": {"password": new_password}})
        
        print(f"✅ Password changed for user ID {user_id}")
        return jsonify({"message": "Password changed successfully"})

    except Exception as e:
        print(f"❌ Change password error: {e}")
        return jsonify({"error": "Internal server error"}), 500

# --- ADMIN ROUTES ---

@app.route("/admin/requests", methods=["GET"])
def get_registration_requests():
    try:
        # NOTE: Implement an authorization check here to ensure only 'admin' can access
        
        requests = list(registration_requests_collection.find().sort("created_at", -1))
        # Convert _id to string for all documents
        requests = [prepare_document(r) for r in requests]

        return jsonify(requests)
    except Exception as e:
        print(f"❌ Get requests error: {e}")
        return jsonify({"error": "Internal server error"}), 500

@app.route("/admin/approve_request", methods=["POST"])
def approve_request():
    try:
        data = request.json
        request_id, user_role = data.get("request_id"), data.get("user_role")

        if user_role != 'admin':
            return jsonify({"error": "Unauthorized"}), 403
        
        obj_id = to_object_id(request_id)
        if not obj_id: return jsonify({"error": "Invalid Request ID format"}), 400

        request_doc = registration_requests_collection.find_one({"_id": obj_id})
        if not request_doc:
            return jsonify({"error": "Request not found"}), 404

        # 1. Move user data to the main users collection
        user_data = {
            "name": request_doc["name"],
            "email": request_doc["email"],
            "password": request_doc["password"],
            "role": request_doc["role"],
            "house_number": request_doc["house_number"],
            "created_at": datetime.now().isoformat()
        }
        users_collection.insert_one(user_data)

        # 2. Delete the request
        registration_requests_collection.delete_one({"_id": obj_id})

        print(f"✅ Registration request approved for {request_doc['email']}")
        return jsonify({"message": "User approved and registered successfully"})

    except Exception as e:
        print(f"❌ Approve request error: {e}")
        return jsonify({"error": "Internal server error"}), 500

@app.route("/admin/reject_request", methods=["POST"])
def reject_request():
    try:
        data = request.json
        request_id, user_role = data.get("request_id"), data.get("user_role")

        if user_role != 'admin':
            return jsonify({"error": "Unauthorized"}), 403

        obj_id = to_object_id(request_id)
        if not obj_id: return jsonify({"error": "Invalid Request ID format"}), 400

        result = registration_requests_collection.delete_one({"_id": obj_id})

        if result.deleted_count == 0:
            return jsonify({"error": "Request not found"}), 404

        print(f"✅ Registration request ID {request_id} rejected.")
        return jsonify({"message": "Registration request rejected successfully"})

    except Exception as e:
        print(f"❌ Reject request error: {e}")
        return jsonify({"error": "Internal server error"}), 500

@app.route("/admin/get_users", methods=["GET"])
def get_users():
    try:
        # Find all users except the 'admin@society.com' user
        users = list(users_collection.find({"email": {"$ne": "admin@society.com"}}).sort("name", 1))
        
        # Prepare and remove password from each user document
        user_list = []
        for user in users:
            user_safe = prepare_document(user)
            user_safe.pop('password', None)
            user_list.append(user_safe)

        return jsonify(user_list)
    except Exception as e:
        print(f"❌ Get users error: {e}")
        return jsonify({"error": "Internal server error"}), 500

@app.route("/admin/change_user_role", methods=["POST"])
def change_user_role():
    try:
        data = request.json
        user_id, new_role, admin_role = data.get("user_id"), data.get("new_role"), data.get("admin_role")

        if admin_role != 'admin':
            return jsonify({"error": "Unauthorized"}), 403
        if new_role not in ['resident', 'worker', 'admin']:
             return jsonify({"error": "Invalid role specified"}), 400

        obj_id = to_object_id(user_id)
        if not obj_id: return jsonify({"error": "Invalid User ID format"}), 400

        result = users_collection.update_one(
            {"_id": obj_id}, 
            {"$set": {"role": new_role}}
        )

        if result.matched_count == 0:
            return jsonify({"error": "User not found"}), 404

        print(f"✅ User ID {user_id} role changed to {new_role} by admin.")
        return jsonify({"message": f"User role updated to {new_role}"})

    except Exception as e:
        print(f"❌ Change role error: {e}")
        return jsonify({"error": "Internal server error"}), 500

@app.route("/admin/get_house_requests", methods=["GET"])
def get_house_requests():
    try:
        # Aggregate to join with the users collection to get the user's name and current house
        pipeline = [
            # Join with users collection
            {"$lookup": {
                "from": "users",
                "localField": "user_id",
                "foreignField": "_id",
                "as": "user_info"
            }},
            # Unwind the user_info array (since user_id is unique, there's only one item)
            {"$unwind": "$user_info"},
            # Project the final desired fields
            {"$project": {
                "_id": {"$toString": "$_id"},
                "user_id": {"$toString": "$user_id"},
                "requested_house_number": 1,
                "status": 1,
                "created_at": 1,
                "user_name": "$user_info.name",
                "user_email": "$user_info.email",
                "current_house_number": "$user_info.house_number"
            }},
            {"$sort": {"created_at": -1}}
        ]
        
        requests = list(house_requests_collection.aggregate(pipeline))
        return jsonify(requests)
    except Exception as e:
        print(f"❌ Get house requests error: {e}")
        return jsonify({"error": "Internal server error"}), 500

@app.route("/admin/process_house_request", methods=["POST"])
def process_house_request():
    try:
        data = request.json
        request_id, status, admin_role = data.get("request_id"), data.get("status"), data.get("admin_role")

        if admin_role != 'admin':
            return jsonify({"error": "Unauthorized"}), 403
        if status not in ['approved', 'rejected']:
             return jsonify({"error": "Invalid status specified"}), 400

        obj_id = to_object_id(request_id)
        if not obj_id: return jsonify({"error": "Invalid Request ID format"}), 400

        # Find the request
        request_doc = house_requests_collection.find_one({"_id": obj_id})
        if not request_doc:
            return jsonify({"error": "Request not found"}), 404
        
        user_obj_id = request_doc["user_id"]
        requested_house = request_doc["requested_house_number"]

        # 1. Update the request status
        house_requests_collection.update_one(
            {"_id": obj_id}, 
            {"$set": {"status": status}}
        )

        # 2. If approved, update the user's house number
        if status == 'approved':
            users_collection.update_one(
                {"_id": user_obj_id},
                {"$set": {"house_number": requested_house}}
            )
            print(f"✅ House change request approved for user {user_obj_id}. New house: {requested_house}")
            message = "House change request approved and user house number updated."
        else:
            print(f"✅ House change request rejected for user {user_obj_id}.")
            message = "House change request rejected."

        return jsonify({"message": message})

    except Exception as e:
        print(f"❌ Process house request error: {e}")
        return jsonify({"error": "Internal server error"}), 500


# --- COMPLAINTS ROUTES ---

@app.route("/submit_complaint", methods=["POST"])
def submit_complaint():
    try:
        user_id = to_object_id(request.form.get("user_id"))
        title = request.form.get("title")
        description = request.form.get("description")
        category = request.form.get("category")
        
        if not all([user_id, title, description, category]):
            return jsonify({"error": "Missing required fields"}), 400

        # Handle file upload
        filename = None
        if 'image' in request.files and request.files['image'].filename != '':
            file = request.files['image']
            if file and allowed_file(file.filename):
                # Generate a secure, unique filename (e.g., hash + extension)
                file_extension = file.filename.rsplit('.', 1)[1].lower()
                filename = secrets.token_hex(16) + '.' + file_extension
                file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
                print(f"✅ File uploaded: {filename}")
            else:
                return jsonify({"error": "Invalid file type"}), 400

        user = users_collection.find_one({"_id": user_id})
        if not user: return jsonify({"error": "User not found"}), 404

        complaint_doc = {
            "user_id": user_id,
            "title": title,
            "description": description,
            "category": category,
            "image_url": filename, # Store the unique filename
            "status": "Open",
            "created_at": datetime.now().isoformat(),
            "last_updated": datetime.now().isoformat()
        }
        
        result = complaints_collection.insert_one(complaint_doc)
        print(f"✅ New complaint submitted with ID: {result.inserted_id}")

        return jsonify({"message": "Complaint submitted successfully", "id": str(result.inserted_id)}), 201
    
    except Exception as e:
        print(f"❌ Submit complaint error: {e}")
        traceback.print_exc()
        return jsonify({"error": "Internal server error"}), 500

@app.route("/get_complaints", methods=["GET"])
def get_complaints():
    try:
        user_role = request.args.get("user_role")
        user_id_str = request.args.get("user_id")
        
        match_query = {}
        if user_role == 'resident' and user_id_str:
            user_id = to_object_id(user_id_str)
            if user_id:
                match_query["user_id"] = user_id
        
        # Aggregation Pipeline to join complaints with user names and like counts
        pipeline = [
            # 1. Filter complaints based on the user_role (if resident)
            {"$match": match_query},
            
            # 2. Join with users collection to get the name of the submitter
            {"$lookup": {
                "from": "users",
                "localField": "user_id",
                "foreignField": "_id",
                "as": "user_info"
            }},
            # 3. Join with likes collection to calculate the like count
            {"$lookup": {
                "from": "complaint_likes",
                "localField": "_id",
                "foreignField": "complaint_id",
                "as": "likes"
            }},
            # 4. Project the final fields
            {"$project": {
                "_id": {"$toString": "$_id"}, # Convert ObjectId to string
                "title": 1,
                "description": 1,
                "category": 1,
                "status": 1,
                "image_url": 1,
                "created_at": 1,
                "last_updated": 1,
                "user_id": {"$toString": "$user_id"},
                "user_name": {"$arrayElemAt": ["$user_info.name", 0]}, # Get the name from the joined array
                "like_count": {"$size": "$likes"}, # Count the number of likes
                "liking_users": {"$map": { # Get a list of user_ids that liked this complaint
                    "input": "$likes",
                    "as": "like",
                    "in": {"$toString": "$$like.user_id"}
                }}
            }},
            # 5. Sort by last_updated (newest first)
            {"$sort": {"last_updated": -1}}
        ]

        complaints = list(complaints_collection.aggregate(pipeline))

        return jsonify(complaints)

    except Exception as e:
        print(f"❌ Get complaints error: {e}")
        traceback.print_exc()
        return jsonify({"error": "Internal server error"}), 500

@app.route("/update_complaint_status", methods=["POST"])
def update_complaint_status():
    try:
        data = request.json
        complaint_id, status, user_role = data.get("id"), data.get("status"), data.get("user_role")
        
        if user_role not in ['admin', 'worker']:
            return jsonify({"error": "Unauthorized"}), 403
        if status not in ['Open', 'in-progress', 'resolved']:
            return jsonify({"error": "Invalid status"}), 400

        obj_id = to_object_id(complaint_id)
        if not obj_id: return jsonify({"error": "Invalid Complaint ID format"}), 400
        
        result = complaints_collection.update_one(
            {"_id": obj_id},
            {"$set": {"status": status, "last_updated": datetime.now().isoformat()}}
        )

        if result.matched_count == 0:
            return jsonify({"error": "Complaint not found"}), 404

        print(f"✅ Complaint ID {complaint_id} status updated to {status} by {user_role}.")
        return jsonify({"message": f"Complaint status updated to {status}"})

    except Exception as e:
        print(f"❌ Update status error: {e}")
        return jsonify({"error": "Internal server error"}), 500

@app.route("/delete_complaint", methods=["POST"])
def delete_complaint():
    try:
        data = request.json
        complaint_id, user_role = data.get("id"), data.get("user_role")
        
        if user_role not in ['admin']:
            return jsonify({"error": "Unauthorized"}), 403

        obj_id = to_object_id(complaint_id)
        if not obj_id: return jsonify({"error": "Invalid Complaint ID format"}), 400

        # 1. Delete associated likes
        likes_collection.delete_many({"complaint_id": obj_id})

        # 2. Delete the complaint
        result = complaints_collection.delete_one({"_id": obj_id})
        
        if result.deleted_count == 0:
            return jsonify({"error": "Complaint not found"}), 404

        print(f"✅ Complaint ID {complaint_id} and associated likes deleted by admin.")
        return jsonify({"message": "Complaint deleted successfully"})

    except Exception as e:
        print(f"❌ Delete complaint error: {e}")
        return jsonify({"error": "Internal server error"}), 500

@app.route("/like_complaint", methods=["POST"])
def like_complaint():
    try:
        data = request.json
        complaint_id_str, user_id_str = data.get("complaint_id"), data.get("user_id")

        if not all([complaint_id_str, user_id_str]):
            return jsonify({"error": "Missing required fields"}), 400

        complaint_obj_id = to_object_id(complaint_id_str)
        user_obj_id = to_object_id(user_id_str)
        
        if not complaint_obj_id or not user_obj_id:
             return jsonify({"error": "Invalid ID format"}), 400

        # Check if the complaint and user exist (optional but good for integrity)
        if not complaints_collection.find_one({"_id": complaint_obj_id}):
            return jsonify({"error": "Complaint not found"}), 404
        if not users_collection.find_one({"_id": user_obj_id}):
            return jsonify({"error": "User not found"}), 404

        # Check if the user already liked it
        existing_like = likes_collection.find_one({
            "complaint_id": complaint_obj_id, 
            "user_id": user_obj_id
        })

        if existing_like:
            # Unlike the complaint
            likes_collection.delete_one({"_id": existing_like["_id"]})
            print(f"✅ User {user_id_str} unliked complaint {complaint_id_str}")
            return jsonify({"message": "Unliked", "action": "unliked"})
        else:
            # Like the complaint
            likes_collection.insert_one({
                "complaint_id": complaint_obj_id, 
                "user_id": user_obj_id, 
                "created_at": datetime.now().isoformat()
            })
            print(f"✅ User {user_id_str} liked complaint {complaint_id_str}")
            return jsonify({"message": "Liked", "action": "liked"})

    except Exception as e:
        print(f"❌ Like/Unlike error: {e}")
        return jsonify({"error": "Internal server error"}), 500


# --- POLLS ROUTES ---

@app.route("/create_poll", methods=["POST"])
def create_poll():
    try:
        data = request.json
        question, options_list, user_id_str = data.get("question"), data.get("options"), data.get("user_id")
        
        if not all([question, options_list, user_id_str]):
            return jsonify({"error": "Missing required fields"}), 400
        
        user_id = to_object_id(user_id_str)
        if not user_id: return jsonify({"error": "Invalid User ID format"}), 400

        # Validate options are a list of strings and have at least two options
        if not isinstance(options_list, list) or len(options_list) < 2:
            return jsonify({"error": "Poll requires at least two options"}), 400
        
        poll_doc = {
            "user_id": user_id,
            "question": question,
            "options": options_list,
            "created_at": datetime.now().isoformat(),
            "is_active": True
        }
        
        result = polls_collection.insert_one(poll_doc)
        print(f"✅ New poll created with ID: {result.inserted_id}")

        return jsonify({"message": "Poll created successfully", "id": str(result.inserted_id)}), 201
    except Exception as e:
        print(f"❌ Create poll error: {e}")
        return jsonify({"error": "Internal server error"}), 500

@app.route("/get_polls", methods=["GET"])
def get_polls():
    try:
        # Get all polls, sort by newest first
        polls = list(polls_collection.find().sort("created_at", -1))
        
        # Convert _id to string for all documents
        polls = [prepare_document(p) for p in polls]
        
        # Get vote counts for all polls
        poll_ids = [p["_id"] for p in polls]
        poll_obj_ids = [to_object_id(id_str) for id_str in poll_ids]
        
        # Aggregate to get vote counts per poll and option
        vote_counts_pipeline = [
            {"$match": {"poll_id": {"$in": poll_obj_ids}}},
            {"$group": {
                "_id": {"poll_id": "$poll_id", "option_index": "$option_index"},
                "count": {"$sum": 1}
            }},
            {"$project": {
                "poll_id": {"$toString": "$_id.poll_id"},
                "option_index": "$_id.option_index",
                "count": 1,
                "_id": 0
            }}
        ]
        vote_counts = list(poll_votes_collection.aggregate(vote_counts_pipeline))
        
        # Restructure votes into a dictionary for easy lookup
        votes_dict = {}
        for vc in vote_counts:
            poll_id = vc["poll_id"]
            if poll_id not in votes_dict:
                votes_dict[poll_id] = {}
            votes_dict[poll_id][vc["option_index"]] = vc["count"]

        # Finalize poll data with vote counts
        for poll in polls:
            poll_id = poll["_id"]
            total_votes = 0
            poll['results'] = []
            
            for i, option in enumerate(poll['options']):
                count = votes_dict.get(poll_id, {}).get(i, 0)
                total_votes += count
                poll['results'].append({
                    "option": option,
                    "count": count
                })
            poll['total_votes'] = total_votes

        # Determine user's vote if user_id is provided in args (for resident view)
        user_id_str = request.args.get("user_id")
        user_obj_id = to_object_id(user_id_str) if user_id_str else None
        
        if user_obj_id:
            user_votes = list(poll_votes_collection.find({"user_id": user_obj_id}))
            user_votes_map = {str(v["poll_id"]): v["option_index"] for v in user_votes}
            
            for poll in polls:
                poll['user_voted_index'] = user_votes_map.get(poll["_id"], -1)

        return jsonify(polls)

    except Exception as e:
        print(f"❌ Get polls error: {e}")
        traceback.print_exc()
        return jsonify({"error": "Internal server error"}), 500

@app.route("/vote_poll", methods=["POST"])
def vote_poll():
    try:
        data = request.json
        poll_id_str, user_id_str, option_index = data.get("poll_id"), data.get("user_id"), data.get("option_index")
        
        if not all([poll_id_str, user_id_str, option_index is not None]):
            return jsonify({"error": "Missing required fields"}), 400

        poll_obj_id = to_object_id(poll_id_str)
        user_obj_id = to_object_id(user_id_str)

        if not poll_obj_id or not user_obj_id:
             return jsonify({"error": "Invalid ID format"}), 400
        
        poll = polls_collection.find_one({"_id": poll_obj_id, "is_active": True})
        if not poll:
            return jsonify({"error": "Poll not found or is closed"}), 404
        
        if option_index < 0 or option_index >= len(poll['options']):
            return jsonify({"error": "Invalid option index"}), 400

        # Check if user already voted (and delete old vote to allow changing vote)
        poll_votes_collection.delete_one({
            "poll_id": poll_obj_id, 
            "user_id": user_obj_id
        })

        # Insert the new vote
        poll_votes_collection.insert_one({
            "poll_id": poll_obj_id,
            "user_id": user_obj_id,
            "option_index": option_index,
            "voted_at": datetime.now().isoformat()
        })
        
        print(f"✅ User {user_id_str} voted on poll {poll_id_str} with option {option_index}")
        return jsonify({"message": "Vote recorded successfully", "new_vote_index": option_index})

    except Exception as e:
        print(f"❌ Vote poll error: {e}")
        return jsonify({"error": "Internal server error"}), 500

@app.route("/close_poll", methods=["POST"])
def close_poll():
    try:
        data = request.json
        poll_id, user_role = data.get("poll_id"), data.get("user_role")

        if user_role not in ['admin']:
            return jsonify({"error": "Unauthorized"}), 403

        obj_id = to_object_id(poll_id)
        if not obj_id: return jsonify({"error": "Invalid Poll ID format"}), 400

        result = polls_collection.update_one(
            {"_id": obj_id}, 
            {"$set": {"is_active": False}}
        )

        if result.matched_count == 0:
            return jsonify({"error": "Poll not found"}), 404

        print(f"✅ Poll ID {poll_id} closed by admin.")
        return jsonify({"message": "Poll closed successfully"})

    except Exception as e:
        print(f"❌ Close poll error: {e}")
        return jsonify({"error": "Internal server error"}), 500

@app.route("/delete_poll", methods=["POST"])
def delete_poll():
    try:
        data = request.json
        poll_id, user_role = data.get("poll_id"), data.get("user_role")

        if user_role not in ['admin']:
            return jsonify({"error": "Unauthorized"}), 403

        obj_id = to_object_id(poll_id)
        if not obj_id: return jsonify({"error": "Invalid Poll ID format"}), 400

        # 1. Delete associated votes
        poll_votes_collection.delete_many({"poll_id": obj_id})
        
        # 2. Delete the poll
        result = polls_collection.delete_one({"_id": obj_id})
        
        if result.deleted_count == 0:
            return jsonify({"error": "Poll not found"}), 404

        print(f"✅ Poll ID {poll_id} and associated votes deleted by admin.")
        return jsonify({"message": "Poll deleted successfully"})

    except Exception as e:
        print(f"❌ Delete poll error: {e}")
        return jsonify({"error": "Internal server error"}), 500


# --- ALERTS/NOTICES ROUTES ---

@app.route("/create_alert", methods=["POST"])
def create_alert():
    try:
        data = request.json
        message, user_id_str, user_role = data.get("message"), data.get("user_id"), data.get("user_role")

        if user_role not in ['admin', 'worker']:
            return jsonify({"error": "Unauthorized"}), 403
        if not all([message, user_id_str]):
            return jsonify({"error": "Missing required fields"}), 400
        
        user_id = to_object_id(user_id_str)
        if not user_id: return jsonify({"error": "Invalid User ID format"}), 400

        alert_doc = {
            "message": message,
            "created_by": user_id,
            "created_at": datetime.now().isoformat()
        }
        
        result = alerts_collection.insert_one(alert_doc)
        print(f"✅ New alert created with ID: {result.inserted_id}")

        return jsonify({"message": "Alert created successfully", "id": str(result.inserted_id)}), 201
    except Exception as e:
        print(f"❌ Create alert error: {e}")
        return jsonify({"error": "Internal server error"}), 500

@app.route("/get_alerts", methods=["GET"])
def get_alerts():
    try:
        # Aggregate to join with the users collection to get the created_by name
        alerts_pipeline = [
            # Join with users collection
            {"$lookup": {
                "from": "users",
                "localField": "created_by",
                "foreignField": "_id",
                "as": "creator"
            }},
            # Unwind the creator array (since created_by is unique, there's only one item)
            {"$unwind": "$creator"},
            # Project the final desired fields
            {"$project": {
                "_id": {"$toString": "$_id"},
                "message": 1,
                "created_at": 1,
                "created_by_name": "$creator.name"
            }},
            {"$sort": {"created_at": -1}}
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

# --- HOUSE CHANGE ROUTES (Resident) ---

@app.route("/request_house_change", methods=["POST"])
def request_house_change():
    try:
        data = request.json
        user_id_str, new_house_number = data.get("user_id"), data.get("new_house_number")
        
        if not all([user_id_str, new_house_number]):
            return jsonify({"error": "Missing required fields"}), 400
        
        user_obj_id = to_object_id(user_id_str)
        if not user_obj_id: return jsonify({"error": "Invalid User ID format"}), 400

        # Check if user already has a pending request
        existing_request = house_requests_collection.find_one({
            "user_id": user_obj_id, 
            "status": "pending"
        })
        if existing_request:
            return jsonify({"error": "You already have a pending house change request."}), 409

        # Check if the requested house number is the user's current house number
        user = users_collection.find_one({"_id": user_obj_id})
        if user and user.get('house_number') == new_house_number:
            return jsonify({"error": "The new house number is the same as your current one."}), 400

        house_requests_collection.insert_one({
            "user_id": user_obj_id,
            "requested_house_number": new_house_number,
            "status": "pending",
            "created_at": datetime.now().isoformat()
        })
        
        print(f"✅ House change request submitted by user {user_id_str} for house {new_house_number}")
        return jsonify({"message": "House change request submitted successfully. Awaiting admin approval."})
    
    except Exception as e:
        print(f"❌ House change request error: {e}")
        return jsonify({"error": "Internal server error"}), 500

# Vercel entry point
if __name__ == "__main__":
    app.run(debug=True)
