import os
from dotenv import load_dotenv
import razorpay

# Load environment variables from .env file
load_dotenv()

key_id = os.getenv("RAZORPAY_KEY_ID")
key_secret = os.getenv("RAZORPAY_KEY_SECRET")

if not key_id or key_id == "your_razorpay_key_id_here" or not key_secret or key_secret == "your_razorpay_key_secret_here":
    print("WARNING: Please replace the Razorpay placeholders in the .env file with your actual Razorpay Test Mode Key ID and Secret.")
    exit(1)

print("Initializing Razorpay client...")
try:
    # Initialize the client
    client = razorpay.Client(auth=(key_id, key_secret))
    
    # We will create a test order of ₹500.00 (which is 50000 paise)
    amount_in_paise = 50000
    currency = "INR"
    receipt_id = "test_receipt_001"
    
    print(f"Creating a test order of {currency} {amount_in_paise / 100:.2f}...")
    
    order_data = {
        "amount": amount_in_paise,
        "currency": currency,
        "receipt": receipt_id,
        "notes": {
            "purpose": "Test connection in isolation",
            "environment": "Development / Test Mode"
        }
    }
    
    order = client.order.create(data=order_data)
    
    print("\n--- Response from Razorpay API ---")
    print(f"Order ID: {order.get('id')}")
    print(f"Status: {order.get('status')}")
    print(f"Amount: {order.get('amount')} {order.get('currency')} (in smallest currency unit)")
    print(f"Receipt: {order.get('receipt')}")
    print("----------------------------------\n")
    print("Razorpay order creation test successful! Check your Razorpay Dashboard (Test Mode) to see the created order.")

except Exception as e:
    print(f"Error connecting to Razorpay API: {e}")
    print("\nPlease verify that your Razorpay keys are correct and that the account is switched to Test Mode.")
