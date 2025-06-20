import os
import threading
import time
from flask import Flask, request, render_template_string, jsonify
from square.client import Square
from square.environment import SquareEnvironment as SquareEnv

app = Flask(__name__)

SQUARE_ACCESS_TOKEN = os.getenv('SQUARE_ACCESS_TOKEN')
SQUARE_LOCATION_ID = os.getenv('SQUARE_LOCATION_ID')
SQUARE_DEVICE_ID = os.getenv('SQUARE_DEVICE_ID', 'da40d603-c2ea-4a65-8cfd-f42e36dab0c7')

# Initialize Square client
square_client = Square(
    token=SQUARE_ACCESS_TOKEN,
    environment=SquareEnv.PRODUCTION  # or 'sandbox' for testing
)

# Enhanced HTML form for price input and terminal pairing
FORM_HTML = '''
<!doctype html>
<title>TipBot7000 Payment</title>
<h2>TipBot7000</h2>
<div style="max-width:400px;margin:auto;">
  <form method="post" action="/pay" style="margin-bottom:2em;">
    <label for="amount"><b>Enter Price to Pay (USD):</b></label><br>
    <input type="number" name="amount" id="amount" step="0.01" min="0.01" required style="width:100%;padding:8px;margin:8px 0;" />
    <button type="submit" style="width:100%;padding:10px;background:#0070ba;color:white;border:none;border-radius:4px;font-size:1.1em;">Pay</button>
  </form>
  <button id="abortBtn" style="display:none;width:100%;padding:10px;background:#e53935;color:white;border:none;border-radius:4px;font-size:1.1em;">Abort Resubmission</button>
  <hr>
  <h3>Pair a Square Terminal</h3>
  <form id="pairForm" onsubmit="pairTerminal(event)">
    <button type="submit" style="width:100%;padding:10px;background:#34a853;color:white;border:none;border-radius:4px;font-size:1.1em;">Pair Terminal</button>
  </form>
  <div id="pairResult" style="margin-top:1em;"></div>
</div>
<script>
let lastCheckoutId = null;
function pairTerminal(event) {
  event.preventDefault();
  document.getElementById('pairResult').innerHTML = 'Pairing...';
  fetch('/pair', {method: 'POST'})
    .then(r => r.json())
    .then(data => {
      if (data.pairing_code) {
        document.getElementById('pairResult').innerHTML =
          '<b>Pairing Code:</b> <span style="font-size:1.5em;">' + data.pairing_code + '</span><br>' +
          '<b>Status:</b> ' + data.status + '<br>' +
          '<b>Expires At:</b> ' + data.expires_at + '<br>' +
          '<b>Instructions:</b> ' + data.instructions +
          (data.device_code_id ? '<br><b>Device ID:</b> ' + data.device_code_id : '');
      } else if (data.error) {
        document.getElementById('pairResult').innerHTML = 'Error: ' + data.error;
      } else {
        document.getElementById('pairResult').innerHTML = JSON.stringify(data);
      }
    })
    .catch(e => {
      document.getElementById('pairResult').innerHTML = 'Error: ' + e;
    });
}
// Listen for pay form submission to show abort button
const payForm = document.querySelector('form[action="/pay"]');
payForm.addEventListener('submit', function(e) {
  setTimeout(() => {
    fetch('/last_checkout_id').then r => r.json()).then(data => {
      if (data.checkout_id) {
        lastCheckoutId = data.checkout_id;
        document.getElementById('abortBtn').style.display = 'block';
      }
    });
  }, 1000);
});
document.getElementById('abortBtn').onclick = function() {
  if (lastCheckoutId) {
    fetch('/abort_resubmit', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({checkout_id: lastCheckoutId})
    }).then(r => r.json()).then(data => {
      alert(data.result || data.error);
      document.getElementById('abortBtn').style.display = 'none';
    });
  }
};
</script>
'''


@app.route('/', methods=['GET'])
def index():
    return render_template_string(FORM_HTML)


# Store the last successful transaction time and checkout_id in memory
last_transaction = {
    'timestamp': None,
    'checkout_id': None,
    'amount': None
}

# Store abort flags for each checkout
abort_flags = {}

# Helper function to cancel and resubmit payment, looping for up to 2 hours or until abort
def schedule_resubmit(checkout_id, amount):
    def task():
        start_time = time.time()
        local_checkout_id = checkout_id  # Use a local variable for mutation
        abort_flags[local_checkout_id] = False
        while time.time() - start_time < 7200:  # 2 hours
            for _ in range(24):  # Check every 5s for abort, total 2min per loop
                if abort_flags.get(local_checkout_id):
                    return
                time.sleep(5)
            # If no new transaction has occurred, check payment status
            if last_transaction['checkout_id'] == local_checkout_id:
                result = square_client.terminal.checkouts.get(local_checkout_id)
                if result.errors is None:
                    checkout = result.checkout
                    if checkout.status == 'PENDING':
                        square_client.terminal.checkouts.cancel(local_checkout_id)
                        idempotency_key = os.urandom(16).hex()
                        body = {
                            "idempotency_key": idempotency_key,
                            "checkout": {
                                "amount_money": {
                                    "amount": amount,
                                    "currency": "USD"
                                },
                                "device_options": {
                                    "device_id": SQUARE_DEVICE_ID,
                                    "skip_receipt_screen": True
                                }
                            }
                        }
                        res = square_client.terminal.checkouts.create(**body)
                        if res.errors is None:
                            new_checkout = res.checkout
                            last_transaction['checkout_id'] = new_checkout.id
                            abort_flags[new_checkout.id] = False
                            local_checkout_id = new_checkout.id
                        else:
                            return  # Stop on error
                else:
                    return  # Stop on error
            else:
                return  # Stop if a new transaction has occurred
        # After 2 hours, cleanup
        abort_flags.pop(local_checkout_id, None)
    threading.Thread(target=task, daemon=True).start()


@app.route('/abort_resubmit', methods=['POST'])
def abort_resubmit():
    data = request.get_json()
    checkout_id = data.get('checkout_id')
    if checkout_id and checkout_id in abort_flags:
        abort_flags[checkout_id] = True
        return jsonify({"result": "Resubmission aborted."})
    return jsonify({"error": "Invalid or missing checkout_id."}), 400


@app.route('/pay', methods=['POST'])
def pay():
    amount = float(request.form['amount'])
    # Convert to cents
    amount_cents = int(amount * 100)
    idempotency_key = os.urandom(16).hex()
    body = {
        "idempotency_key": idempotency_key,
        "checkout": {
            "amount_money": {
                "amount": amount_cents,
                "currency": "USD"
            },
            "device_options": {
                "device_id": SQUARE_DEVICE_ID,
                "skip_receipt_screen": True
            }
        }
    }
    result = square_client.terminal.checkouts.create(**body)
    if result.errors is None:
        checkout = result.checkout
        # Save checkout_id and amount for confirmation
        last_transaction['timestamp'] = time.time()
        last_transaction['checkout_id'] = checkout.id
        last_transaction['amount'] = amount_cents
        # Schedule cancel/resubmit logic
        schedule_resubmit(checkout.id, amount_cents)
        return jsonify({"checkout_id": checkout.id, "amount": amount_cents})
    else:
        return f"Error: {result.errors}", 400


@app.route('/confirm', methods=['POST'])
def confirm():
    data = request.get_json()
    checkout_id = data['data']['object']['payment']['terminal_checkout_id']
    amount = data['data']['object']['payment']['amount_money']['amount']
    # Check payment status
    result = square_client.terminal.checkouts.get(
        checkout_id
    )
    if result.errors is None:
        checkout = result.checkout
        if checkout.status == 'COMPLETED':
            # Update last transaction to prevent resubmission
            last_transaction['timestamp'] = time.time()
            last_transaction['checkout_id'] = checkout_id
            last_transaction['amount'] = amount
            # Create a new payment for the same amount
            payment_body = {
                "idempotency_key": os.urandom(16).hex(),
                "checkout": {
                    "amount_money": {
                        "amount": amount,
                        "currency": "USD"
                    },
                    "device_options": {
                        "device_id": SQUARE_DEVICE_ID,
                        "skip_receipt_screen": True
                    }
                }
            }
            pay_result = square_client.terminal.checkouts.create(**payment_body)
            if pay_result.errors is None:
                return jsonify({"result": "Transaction successful!"})
            else:
                return f"Transaction failed: {pay_result.errors}", 400
        else:
            return jsonify({"result": "Payment not completed yet."})
    else:
        return f"Error: {result.errors}", 400


@app.route('/pair', methods=['POST'])
def pair_terminal():
    """
    Initiates pairing with a Square Terminal and returns the device code and
    pairing instructions. The user must enter the pairing code on the physical
    terminal to complete pairing. After pairing, the device_id can be retrieved
    by polling the device code status.
    """
    idempotency_key = os.urandom(16).hex()
    body = {
        "idempotency_key": idempotency_key,
        "device_code": {
            "name": "TipBot7000 Terminal",
            "product_type": "TERMINAL_API"
        }
    }
    result = square_client.devices.codes.create(**body)
    if result.errors is None:
        device_code_info = result.device_code
        return jsonify({
            "pairing_code": device_code_info.code,
            "status": device_code_info.status,
            "expires_at": device_code_info.pair_by,
            "device_code_id": device_code_info.id,
            "instructions": (
                "Enter this pairing code on your Square Terminal to complete "
                "pairing. After pairing, use the /device_status endpoint with "
                "the device_code_id to retrieve the device_id."
            )
        })
    else:
        return f"Error: {result.errors}", 400


@app.route('/device_status', methods=['GET'])
def device_status():
    """
    Polls the status of a device code and returns the device_id if paired.
    Expects a query parameter: device_code_id
    """
    device_code_id = request.args.get('device_code_id')
    if not device_code_id:
        return jsonify({"error": "Missing device_code_id parameter."}), 400
    result = square_client.devices.codes.get(device_code_id)
    if result.errors is None:
        device_code_info = result.device_code
        return jsonify({
            "status": device_code_info.status,
            "device_id": getattr(device_code_info, 'device_id', None)
        })
    else:
        return f"Error: {result.errors}", 400


@app.route('/last_checkout_id')
def last_checkout_id():
    return jsonify({"checkout_id": last_transaction['checkout_id']})


if __name__ == '__main__':
    app.run(debug=True)
