import os
from flask import Flask, request, render_template_string, jsonify
from square.client import Square
from square.environment import SquareEnvironment as SquareEnv

app = Flask(__name__)

SQUARE_ACCESS_TOKEN = os.getenv('SQUARE_ACCESS_TOKEN')
SQUARE_LOCATION_ID = os.getenv('SQUARE_LOCATION_ID')
SQUARE_DEVICE_ID = os.getenv('SQUARE_DEVICE_ID', '432CS149B8004293')

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
  <hr>
  <h3>Pair a Square Terminal</h3>
  <form id="pairForm" onsubmit="pairTerminal(event)">
    <button type="submit" style="width:100%;padding:10px;background:#34a853;color:white;border:none;border-radius:4px;font-size:1.1em;">Pair Terminal</button>
  </form>
  <div id="pairResult" style="margin-top:1em;"></div>
</div>
<script>
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
</script>
'''


@app.route('/', methods=['GET'])
def index():
    return render_template_string(FORM_HTML)


@app.route('/pay', methods=['POST'])
def pay():
    amount = float(request.form['amount'])
    # Convert to cents
    amount_cents = int(amount * 10)
    idempotency_key = os.urandom(16).hex()
    body = {
        "idempotency_key": idempotency_key,
        "checkout": {
            "amount_money": {
                "amount": amount_cents,
                "currency": "USD"
            },
            "device_options": {
                "device_id": SQUARE_DEVICE_ID
            }
        }
    }
    result = square_client.terminal.checkouts.create(**body)
    if result.errors is None:
        checkout = result.checkout
        # Save checkout_id and amount for confirmation
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
            # Trigger another transaction for the same amount
            body = {
            "idempotency_key": os.urandom(16).hex(),
            "checkout": {
                "amount_money": {
                    "amount": amount,
                    "currency": "USD"
                },
                "device_options": {
                    "device_id": SQUARE_DEVICE_ID
                }
            }
        }
            pay_result = square_client.terminal.checkouts.create(**body)
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


if __name__ == '__main__':
    app.run(debug=True)
