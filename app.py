from flask import Flask, jsonify, request, render_template
from flask_cors import CORS
import mysql.connector
from datetime import datetime
import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# Email config
SENDER_EMAIL = "Lettytanyah01@gmail.com"
SENDER_PASSWORD = os.environ.get('EMAIL_PASSWORD')
PHARMACIST_EMAILS = [
    "tanyahletty01@gmail.com",
    "Staiceymurandu6@gmail.com",
    "Chivimadian@gmail.com"
]

app = Flask(__name__)
CORS(app)

def get_db():
    return mysql.connector.connect(
        host=os.environ.get('DB_HOST', 'localhost'),
        port=int(os.environ.get('DB_PORT', 3306)),
        user=os.environ.get('DB_USER', 'root'),
        password=os.environ.get('DB_PASSWORD', ''),
        database=os.environ.get('DB_NAME', 'medical_vending_db')
    )

def send_email(subject, body):
    try:
        msg = MIMEMultipart()
        msg['From'] = SENDER_EMAIL
        msg['To'] = ", ".join(PHARMACIST_EMAILS)
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'plain'))
        server = smtplib.SMTP_SSL('smtp.gmail.com', 465)
        server.login(SENDER_EMAIL, SENDER_PASSWORD)
        server.sendmail(SENDER_EMAIL, PHARMACIST_EMAILS, msg.as_string())
        server.quit()
        print("Email sent successfully")
    except Exception as e:
        print("Email failed: " + str(e))

@app.route('/verify-rfid/<rfid_uid>', methods=['GET'])
def verify_rfid(rfid_uid):
    db = get_db()
    cursor = db.cursor(dictionary=True)
    try:
        cursor.execute("""
            SELECT
                p.prescription_id,
                p.barcode,
                p.quantity_prescribed,
                p.dosage_instructions,
                p.status,
                p.date_expires,
                p.refills_allowed,
                p.refills_used,
                m.brand_name,
                m.dosage_strength,
                m.medication_id,
                i.quantity_available,
                i.slot_number,
                pt.first_name,
                pt.last_name,
                pt.patient_id,
                pt.hospital_id,
                d.full_name as doctor_name
            FROM Prescription p
            JOIN Patient pt ON p.patient_id = pt.patient_id
            JOIN Medication m ON p.medication_id = m.medication_id
            JOIN Inventory i ON i.medication_id = m.medication_id
                AND i.machine_id = 1
            JOIN Doctor d ON p.doctor_id = d.doctor_id
            WHERE pt.rfid_uid = %s
              AND p.status = 'pending'
              AND p.date_expires >= CURDATE()
              AND i.quantity_available > 0
            ORDER BY p.date_issued DESC
            LIMIT 1
        """, (rfid_uid,))
        result = cursor.fetchone()
        if result:
            print("RFID MATCH: " + result['first_name'])
            print("Drug: " + result['brand_name'])
            return jsonify({"status": "valid", "data": result})

        cursor.execute("""
            SELECT pt.first_name, pt.last_name, p.status,
                   p.date_expires, i.quantity_available
            FROM Patient pt
            LEFT JOIN Prescription p ON pt.patient_id = p.patient_id
            LEFT JOIN Medication m ON p.medication_id = m.medication_id
            LEFT JOIN Inventory i ON i.medication_id = m.medication_id
            WHERE pt.rfid_uid = %s
            LIMIT 1
        """, (rfid_uid,))
        debug = cursor.fetchone()

        if not debug:
            message = "RFID card not registered to any patient"
        elif debug['status'] == 'dispensed':
            message = "Prescription already dispensed"
        elif debug['quantity_available'] == 0:
            message = "Medication out of stock"
        else:
            message = "No active prescription found"

        return jsonify({"status": "invalid", "message": message}), 404

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        db.close()

@app.route('/verify-barcode/<barcode>', methods=['GET'])
def verify_barcode(barcode):
    db = get_db()
    cursor = db.cursor(dictionary=True)
    try:
        cursor.execute("""
            SELECT
                p.prescription_id,
                p.patient_id,
                p.quantity_prescribed,
                p.dosage_instructions,
                p.status,
                p.date_expires,
                m.brand_name,
                m.dosage_strength,
                m.medication_id,
                i.quantity_available,
                i.slot_number,
                pt.first_name,
                pt.last_name
            FROM Prescription p
            JOIN Medication m ON p.medication_id = m.medication_id
            JOIN Inventory i ON i.medication_id = m.medication_id
                AND i.machine_id = 1
            JOIN Patient pt ON p.patient_id = pt.patient_id
            WHERE p.barcode = %s
              AND p.status = 'pending'
              AND p.date_expires >= CURDATE()
        """, (barcode,))
        result = cursor.fetchone()
        if result:
            return jsonify({"status": "valid", "data": result})
        return jsonify({"status": "invalid", "message": "Barcode not found or expired"}), 404
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        db.close()

@app.route('/dispense-prescription', methods=['POST'])
def dispense_prescription():
    data = request.json
    db = get_db()
    cursor = db.cursor()
    try:
        cursor.execute("""
            INSERT INTO Prescription_Transaction
            (prescription_id, patient_id, medication_id,
             machine_id, barcode_scanned,
             quantity_dispensed, status)
            VALUES (%s, %s, %s, %s, %s, %s, 'success')
        """, (data['prescription_id'], data['patient_id'],
              data['medication_id'], data['machine_id'],
              data['barcode'], data['quantity']))

        cursor.execute("""
            SELECT refills_allowed, refills_used
            FROM Prescription
            WHERE prescription_id = %s
        """, (data['prescription_id'],))
        rx = cursor.fetchone()

        refills_used = rx[1] + 1

        if refills_used >= rx[0]:
            cursor.execute("""
                UPDATE Prescription
                SET status = 'dispensed',
                    refills_used = %s
                WHERE prescription_id = %s
            """, (refills_used, data['prescription_id']))
        else:
            cursor.execute("""
                UPDATE Prescription
                SET refills_used = %s
                WHERE prescription_id = %s
            """, (refills_used, data['prescription_id']))

        cursor.execute("""
            UPDATE Inventory
            SET quantity_available = quantity_available - %s
            WHERE medication_id = %s AND machine_id = %s
        """, (data['quantity'], data['medication_id'], data['machine_id']))

        db.commit()

        send_email(
            "Medication Dispensed - Medical Vending Machine",
            "A dispense event occurred.\n\nPrescription ID: " + str(data['prescription_id']) +
            "\nPatient ID: " + str(data['patient_id']) +
            "\nMedication ID: " + str(data['medication_id']) +
            "\nQuantity: " + str(data['quantity']) +
            "\nTime: " + str(datetime.now())
        )

        return jsonify({"status": "success", "message": "Dispensed successfully"})
    except Exception as e:
        db.rollback()
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        db.close()

@app.route('/inventory', methods=['GET'])
def get_inventory():
    db = get_db()
    cursor = db.cursor(dictionary=True)
    try:
        cursor.execute("""
            SELECT
                i.inventory_id,
                i.slot_number,
                i.quantity_available,
                i.reorder_level,
                i.max_capacity,
                i.expiry_date,
                i.last_restocked_date,
                m.brand_name,
                m.dosage_strength,
                m.drug_type,
                m.unit_price
            FROM Inventory i
            JOIN Medication m ON i.medication_id = m.medication_id
            WHERE i.machine_id = 1
            ORDER BY i.slot_number
        """)
        return jsonify(cursor.fetchall())
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        db.close()

@app.route('/alerts', methods=['GET'])
def get_alerts():
    db = get_db()
    cursor = db.cursor(dictionary=True)
    try:
        cursor.execute("""
            SELECT
                a.alert_id,
                a.alert_type,
                a.current_quantity,
                a.reorder_level,
                a.alert_message,
                a.created_at,
                m.brand_name
            FROM Alert a
            JOIN Medication m ON a.medication_id = m.medication_id
            WHERE a.is_resolved = FALSE
            ORDER BY a.created_at DESC
        """)
        alerts = cursor.fetchall()
        if len(alerts) > 0:
            alert_list = "\n".join([
                "- " + a['brand_name'] + ": " + str(a['current_quantity']) +
                " remaining (reorder at " + str(a['reorder_level']) + ")"
                for a in alerts
            ])
            send_email(
                "LOW STOCK ALERT - Medical Vending Machine",
                "The following medications need restocking:\n\n" + alert_list +
                "\n\nPlease restock as soon as possible."
            )
        for alert in alerts:
    if alert.get('created_at'):
        alert['created_at'] = str(alert['created_at'])
    if alert.get('resolved_at'):
        alert['resolved_at'] = str(alert['resolved_at'])
         return jsonify(alerts)
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        db.close()

@app.route('/restock', methods=['POST'])
def restock():
    data = request.json
    db = get_db()
    cursor = db.cursor()
    try:
        cursor.execute("""
            SELECT quantity_available FROM Inventory
            WHERE inventory_id = %s
        """, (data['inventory_id'],))
        row = cursor.fetchone()
        qty_before = row[0]
        qty_after = qty_before + data['quantity_added']

        cursor.execute("""
            INSERT INTO Restock_Log
            (inventory_id, staff_id, quantity_added,
             quantity_before, quantity_after, new_expiry_date)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (data['inventory_id'], data['staff_id'],
              data['quantity_added'], qty_before,
              qty_after, data['new_expiry_date']))

        cursor.execute("""
            UPDATE Inventory
            SET quantity_available = %s,
                expiry_date = %s,
                last_restocked_date = CURDATE(),
                last_restocked_by = %s
            WHERE inventory_id = %s
        """, (qty_after, data['new_expiry_date'],
              data['staff_id'], data['inventory_id']))

        cursor.execute("""
            UPDATE Alert
            SET is_resolved = TRUE,
                resolved_at = NOW(),
                resolved_by = %s
            WHERE inventory_id = %s
              AND is_resolved = FALSE
        """, (data['staff_id'], data['inventory_id']))

        db.commit()
        return jsonify({
            "status": "success",
            "quantity_before": qty_before,
            "quantity_after": qty_after
        })
    except Exception as e:
        db.rollback()
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        db.close()

@app.route('/stats', methods=['GET'])
def get_stats():
    db = get_db()
    cursor = db.cursor(dictionary=True)
    try:
        cursor.execute(
            "SELECT COUNT(*) as total FROM Medication "
            "WHERE is_active = TRUE"
        )
        total_meds = cursor.fetchone()['total']

        cursor.execute("""
            SELECT COUNT(*) as total FROM Inventory
            WHERE quantity_available <= reorder_level
        """)
        low_stock = cursor.fetchone()['total']

        cursor.execute("""
            SELECT COUNT(*) as total FROM Inventory
            WHERE quantity_available = 0
        """)
        out_of_stock = cursor.fetchone()['total']

        cursor.execute("""
            SELECT COUNT(*) as total
            FROM Prescription_Transaction
            WHERE DATE(dispensed_at) = CURDATE()
            AND status = 'success'
        """)
        rx_today = cursor.fetchone()['total']

        return jsonify({
            "total_medications": total_meds,
            "low_stock_count":   low_stock,
            "out_of_stock":      out_of_stock,
            "rx_today":          rx_today
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        db.close()

@app.route('/recent-transactions', methods=['GET'])
def recent_transactions():
    db = get_db()
    cursor = db.cursor(dictionary=True)
    try:
        cursor.execute("""
            SELECT
                pt.first_name,
                pt.last_name,
                m.brand_name,
                m.dosage_strength,
                t.quantity_dispensed as quantity,
                t.dispensed_at,
                t.status
            FROM Prescription_Transaction t
            JOIN Patient pt ON t.patient_id = pt.patient_id
            JOIN Medication m ON t.medication_id = m.medication_id
            ORDER BY t.dispensed_at DESC
            LIMIT 15
        """)
        return jsonify(cursor.fetchall())
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        db.close()

@app.route('/patients', methods=['GET'])
def get_patients():
    db = get_db()
    cursor = db.cursor(dictionary=True)
    try:
        cursor.execute("""
            SELECT patient_id, first_name,
                   last_name, hospital_id
            FROM Patient ORDER BY first_name
        """)
        return jsonify(cursor.fetchall())
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        db.close()

@app.route('/doctors', methods=['GET'])
def get_doctors():
    db = get_db()
    cursor = db.cursor(dictionary=True)
    try:
        cursor.execute("""
            SELECT doctor_id, full_name,
                   specialization, hospital_department
            FROM Doctor
            WHERE is_active = TRUE
            ORDER BY full_name
        """)
        return jsonify(cursor.fetchall())
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        db.close()

@app.route('/prescription-drugs', methods=['GET'])
def prescription_drugs():
    db = get_db()
    cursor = db.cursor(dictionary=True)
    try:
        cursor.execute("""
            SELECT medication_id, brand_name,
                   generic_name, dosage_strength,
                   dosage_form, unit_price
            FROM Medication
            WHERE drug_type = 'prescription'
              AND is_active = TRUE
            ORDER BY brand_name
        """)
        return jsonify(cursor.fetchall())
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        db.close()

@app.route('/create-prescription', methods=['POST'])
def create_prescription():
    data = request.json
    db = get_db()
    cursor = db.cursor(dictionary=True)
    try:
        cursor.execute(
            "SELECT COUNT(*) as total FROM Prescription"
        )
        count = cursor.fetchone()['total']
        barcode = ("RX-" + str(datetime.now().year) +
                   "-" + str(count + 1).zfill(5))

        cursor.execute("""
            INSERT INTO Prescription
            (barcode, patient_id, doctor_id,
             medication_id, dosage_instructions,
             quantity_prescribed, refills_allowed,
             date_issued, date_expires)
            VALUES (%s, %s, %s, %s, %s, %s, %s,
                    CURDATE(), %s)
        """, (barcode, data['patient_id'],
              data['doctor_id'], data['medication_id'],
              data['dosage_instructions'],
              data['quantity_prescribed'],
              data.get('refills_allowed', 0),
              data['date_expires']))
        db.commit()

        cursor.execute("""
            SELECT
                p.prescription_id, p.barcode,
                p.dosage_instructions,
                p.quantity_prescribed,
                p.date_issued, p.date_expires,
                pt.first_name, pt.last_name,
                pt.hospital_id,
                m.brand_name, m.dosage_strength,
                d.full_name as doctor_name
            FROM Prescription p
            JOIN Patient pt ON p.patient_id = pt.patient_id
            JOIN Medication m ON p.medication_id = m.medication_id
            JOIN Doctor d ON p.doctor_id = d.doctor_id
            WHERE p.barcode = %s
        """, (barcode,))
        prescription = cursor.fetchone()
        return jsonify({"status": "success", "prescription": prescription})
    except Exception as e:
        db.rollback()
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        db.close()

@app.route('/doctor-portal')
def doctor_portal():
    return render_template('doctor_portal.html')

@app.route('/dashboard')
def dashboard():
    return render_template('dashboard.html')

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)