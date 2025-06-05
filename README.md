# TipBot7000

This is a Python web application that allows users to input a price, uses Square's Terminal API to capture payment, and provides an endpoint to confirm payment and trigger a follow-up transaction for the same amount.

## Features
- Input a price and initiate a payment using Square Terminal API
- Endpoint to confirm payment success
- Triggers a second transaction for the same amount upon confirmation

## Requirements
- Python 3.8+
- Flask
- Requests
- Square Python SDK

## Setup
1. Install dependencies:
   ```sh
   pip install -r requirements.txt
   ```
2. Set your Square API credentials as environment variables:
   - `SQUARE_ACCESS_TOKEN`
   - `SQUARE_LOCATION_ID`

## Running the App
```sh
python app.py
```

## Endpoints
- `/` : Input price and start payment
- `/confirm` : Confirm payment and trigger follow-up transaction

## Security
- Never commit your Square API credentials to source control.
- Use environment variables for all sensitive data.
