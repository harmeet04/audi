import os
import random
import qrcode
import json
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

app = Flask(__name__)
app.secret_key = 'airforce_secret_key'
# Uses Cloud Database if available, otherwise falls back to local SQLite
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///cinema.db')
app.config['UPLOAD_FOLDER'] = 'static/uploads'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

app.jinja_env.filters['chr'] = chr

db = SQLAlchemy(app)

# --- Models ---
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    mobile = db.Column(db.String(15), unique=True, nullable=False)
    category = db.Column(db.String(50))
    rank = db.Column(db.String(50))
    name = db.Column(db.String(100))

class Movie(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(100))
    image_file = db.Column(db.String(100))
    description = db.Column(db.Text)
    shows = db.relationship('Showtime', backref='movie', lazy=True)

class Showtime(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    movie_id = db.Column(db.Integer, db.ForeignKey('movie.id'), nullable=False)
    show_date = db.Column(db.String(20))
    show_time = db.Column(db.String(10))

class Booking(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    showtime_id = db.Column(db.Integer, db.ForeignKey('showtime.id'))
    seat_numbers = db.Column(db.String(200))
    qr_code = db.Column(db.String(100))
    is_scanned = db.Column(db.Boolean, default=False)

RANKS = {
    "Officer": ["Air Commodore", "Group Captain", "Wing Commander", "Squadron Leader", "Flight Lieutenant", "Flying Officer"],
    "Airmen": ["Honorary Flight Lieutenant", "Honorary Flying Officer", "Master Warrant Officer", "Warrant Officer", "Junior Warrant Officer", "Sergeant", "Corporal", "Leading Aircraftman", "Aircraftman", "Agniveer"]
}
with app.app_context():
    db.create_all()
    # Create upload folder if it doesn't exist
    if not os.path.exists(app.config['UPLOAD_FOLDER']):
        os.makedirs(app.config['UPLOAD_FOLDER'])
# --- Routes ---

@app.route('/')
def index():
    movie = Movie.query.order_by(Movie.id.desc()).first()
    return render_template('index.html', movie=movie)

# --- ADMIN ROUTES (Movies Only) ---
@app.route('/admin-login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        if request.form.get('password') == 'admin':
            session['is_admin'] = True
            return redirect(url_for('admin'))
        else:
            flash("Invalid Admin Password")
    return render_template('admin_login.html')

@app.route('/admin', methods=['GET', 'POST'])
def admin():
    if not session.get('is_admin'): return redirect(url_for('admin_login'))

    if request.method == 'POST':
        title = request.form.get('title')
        desc = request.form.get('description')
        image = request.files['image']
        dates = request.form.getlist('date[]')
        times = request.form.getlist('time[]')
        
        if image and title:
            filename = image.filename
            image.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
            Movie.query.delete()
            Showtime.query.delete()
            Booking.query.delete()
            new_movie = Movie(title=title, description=desc, image_file=filename)
            db.session.add(new_movie)
            db.session.flush()
            for d, t in zip(dates, times):
                if d and t: db.session.add(Showtime(movie_id=new_movie.id, show_date=d, show_time=t))
            db.session.commit()
            flash("Movie and Shows updated!")
    return render_template('admin.html')

@app.route('/logout-admin')
def logout_admin():
    session.pop('is_admin', None)
    return redirect(url_for('index'))

# --- MANAGER ROUTES (Scanner & Occupancy) ---
@app.route('/manager-login', methods=['GET', 'POST'])
def manager_login():
    if request.method == 'POST':
        if request.form.get('password') == 'manager':
            session['is_manager'] = True
            return redirect(url_for('manager'))
        else:
            flash("Invalid Manager Password")
    return render_template('manager_login.html')

@app.route('/manager')
def manager():
    if not session.get('is_manager'): return redirect(url_for('manager_login'))
    return render_template('manager.html')

@app.route('/logout-manager')
def logout_manager():
    session.pop('is_manager', None)
    return redirect(url_for('index'))

@app.route('/scanner')
def scanner():
    if not session.get('is_manager'): return redirect(url_for('manager_login'))
    return render_template('scanner.html')

@app.route('/occupancy')
def occupancy():
    if not session.get('is_manager'): return redirect(url_for('manager_login'))
    
    movie = Movie.query.order_by(Movie.id.desc()).first()
    shows = movie.shows if movie else []
    
    selected_show_id = request.args.get('show_id')
    selected_show = None
    filled_seats = []
    booked_seats = []
    
    if shows:
        if selected_show_id:
            selected_show = Showtime.query.get(selected_show_id)
        else:
            selected_show = shows[0]

        bookings = Booking.query.filter_by(showtime_id=selected_show.id).all()
        for b in bookings:
            seats = b.seat_numbers.split(',')
            booked_seats.extend(seats)
            if b.is_scanned:
                filled_seats.extend(seats)
                
    return render_template('occupancy.html', 
                         shows=shows, 
                         selected_show=selected_show, 
                         filled_seats=filled_seats, 
                         booked_seats=booked_seats)

# --- API for Scanner (Manager Only) ---
@app.route('/api/scan_ticket', methods=['POST'])
def scan_ticket():
    if not session.get('is_manager'): return jsonify({'status': 'error', 'message': 'Unauthorized'})
    
    data = request.json
    booking_id = data.get('booking_id')
    booking = Booking.query.get(booking_id)
    if booking:
        if booking.is_scanned:
            return jsonify({'status': 'error', 'message': 'Already Scanned!'})
        booking.is_scanned = True
        db.session.commit()
        return jsonify({'status': 'success', 'seats': booking.seat_numbers})
    return jsonify({'status': 'error', 'message': 'Invalid Ticket'})

# --- USER ROUTES (Login/Book) ---
@app.route('/login', methods=['GET', 'POST'])
def login():
    show_id = request.args.get('show_id')
    if show_id: session['pending_show_id'] = show_id

    if request.method == 'POST':
        mobile = request.form.get('mobile')
        user = User.query.filter_by(mobile=mobile).first()
        if user:
            session['user_id'] = user.id
            session['user_name'] = user.name
            sid = session.get('pending_show_id')
            return redirect(url_for('book_tickets', show_id=sid)) if sid else redirect(url_for('index'))
        else:
            return render_template('login.html', step='register', mobile=mobile, ranks=RANKS)
    return render_template('login.html', step='login', ranks=RANKS)

@app.route('/register', methods=['POST'])
def register():
    mobile = request.form.get('mobile')
    category = request.form.get('category')
    rank = request.form.get('rank')
    name = request.form.get('name')
    new_user = User(mobile=mobile, category=category, rank=rank, name=name)
    db.session.add(new_user)
    db.session.commit()
    session['user_id'] = new_user.id
    session['user_name'] = new_user.name
    sid = session.get('pending_show_id')
    return redirect(url_for('book_tickets', show_id=sid)) if sid else redirect(url_for('index'))

@app.route('/book/<int:show_id>', methods=['GET', 'POST'])
def book_tickets(show_id):
    if 'user_id' not in session: return redirect(url_for('login', show_id=show_id))
    user = User.query.get(session['user_id'])
    if not user:
        session.clear()
        return redirect(url_for('login', show_id=show_id))

    show = Showtime.query.get_or_404(show_id)
    movie = show.movie
    existing_bookings = Booking.query.filter_by(showtime_id=show.id).all()
    booked_seats = []
    for b in existing_bookings: booked_seats.extend(b.seat_numbers.split(','))

    if request.method == 'POST':
        selected_seats = request.form.get('selected_seats')
        if not selected_seats:
            flash("Please select seats")
            return redirect(request.url)
        
        new_booking = Booking(user_id=user.id, showtime_id=show.id, seat_numbers=selected_seats, is_scanned=False)
        db.session.add(new_booking)
        db.session.commit()
        
        qr_json = json.dumps({"bid": new_booking.id, "seats": selected_seats})
        qr_img = qrcode.make(qr_json)
        qr_filename = f"qr_{new_booking.id}.png"
        qr_path = os.path.join('static', 'uploads', qr_filename)
        qr_img.save(qr_path)
        
        new_booking.qr_code = qr_filename
        db.session.commit()
        
        return render_template('ticket.html', booking=new_booking, movie=movie, show=show, user=user)

    return render_template('booking.html', movie=movie, show=show, booked_seats=booked_seats, user_category=user.category)

if __name__ == '__main__':
    app.run(debug=True, use_reloader=False)
