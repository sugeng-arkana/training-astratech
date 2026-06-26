#!/usr/bin/env python3
"""
Debug Test Script for Cargoson Courier Fetching
===============================================

This script helps test the debug logging functionality.
Run this script to verify that the logging is working correctly.

Usage:
    python3 debug_test.py

Make sure to:
1. Set your Odoo log level to INFO or DEBUG
2. Check your Odoo logs after running the courier refresh
3. Look for the debug messages starting with "=== CARGOSON"
"""

import logging

# Configure logging to see debug messages
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)

def test_logging():
    """Test that logging is working correctly"""
    logger.info("=== DEBUG LOGGING TEST ===")
    logger.info("This is a test message to verify logging is working")
    logger.info("If you see this message, logging is configured correctly")
    logger.info("=== END DEBUG LOGGING TEST ===")

if __name__ == "__main__":
    test_logging()
    print("\nDebug logging test completed.")
    print("Check your Odoo logs for messages starting with '=== CARGOSON'")
    print("\nTo test the courier refresh:")
    print("1. Go to Inventory > Configuration > Delivery Methods")
    print("2. Edit a Cargoson delivery method")
    print("3. Click the refresh button next to 'Cargoson Couriers'")
    print("4. Check the Odoo logs for detailed debug information")
