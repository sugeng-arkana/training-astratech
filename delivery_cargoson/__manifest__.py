{
    'name': "Shipping & Freight Prices - All Carriers",
    'summary': """
        Multi-carrier freight shipping connector by Cargoson's transport management software (TMS).
        Connect all your carriers via API or EDI. Request new carrier integrations for free.
        Simple freight management from one freight management software: Transport orders, labels, tracking, statistics, notifications. All freight modes: parcel, road FTL, road LTL, air, sea, rail.
        All your freight prices from different carriers visible from one window, directly in Odoo.
        Prices are fetched from carrier APIs, or uploaded to Cargoson and calculated using Cargoson's powerful freight rate management engine.
        Not a middleman or a forwarder - all prices and carrier contracts are agreed between you and your carriers directly.
        Extensive carrier connector library:
        Schenker
        DSV Road
        DHL Freight
        ACE Logistics
        Rhenus Logistics
        Girteka
        DLW
        Matkahuolto
        Kaukokiito
        Kuehne Nagel (Kühne+Nagel)
        Röhlig Suus
        Bring
        Raben
        Emons
        Dachser
        GLS
        Geodis
        FedEx
        TNT
        DHL Express
        DSV Xpress
        UPS
        DPD
        BRT (Bartolini)
        Bolt
        Wolt
        Delamode
        Venipak
        NTG
        Posti Jakelu
        Itella
        Omniva
        Postnord
        DHL Paket
        Deutsche Post
        Speedy
        Econt
        and 1500 more…
    """,
    'description': """
        Cargoson Transport Management System (TMS) Integration
        ===================================================

        Compare, order transport and communicate with all carriers using Cargoson's comprehensive transport management system, directly integrated into Odoo. This connector brings powerful shipping management capabilities right into your workflow, and enables seamless carrier integration, automated booking, and shipment tracking.

        Key Features
        -----------

        Direct Carrier Integration
        * Connect to 1,500+ carriers across Europe and US through a single integration
        * Direct API or EDI integration with carriers - no manual data entry needed
        * Request new carrier integrations for free by contacting support@cargoson.com
        * Maintain your direct carrier relationships and use your own negotiated rates
        * No middleman or markups - work directly with your preferred carriers
        * Support for all freight modes: parcel, road FTL, road LTL, air, sea, and rail

        Price Management & Comparison
        * Upload and manage your carrier-specific pricelists
        * Compare actual carrier rates side-by-side
        * Get instant price calculations based on your negotiated rates
        * Request and manage spot prices from carriers
        * Automated transport cost calculation from your existing price lists

        Shipment Management
        * One-click conversion of sales/purchase orders into transport orders
        * Automated shipping label generation
        * Digital documentation: CMRs, e-Waybills (eFTI approved), and DGD
        * Centralized document storage and management
        * Branded email notifications for customers, colleagues, and partners

        Tracking & Monitoring
        * Real-time shipment tracking across all carriers
        * Smart milestone tracking system
        * Daily delayed shipment reports
        * Comprehensive transport statistics and reporting
        * Carbon footprint monitoring for all shipments

        Automation Features
        ------------------

        Pre-shipment:
        * Automatic shipment creation from Odoo sales/purchase orders
        * Dynamic shipping options display
        * Integrated parcel locker network access
        * Team collaboration tools for shipping coordination
        * Automated carrier selection based on custom rules

        Post-shipment:
        * Automated label generation and distribution
        * Real-time tracking link generation
        * Automatic status updates in Odoo
        * Automated customer notifications
        * Invoice comparison with pre-calculated amounts

        Integration Benefits
        ------------------

        * Seamless Odoo Integration: Manage all shipping tasks without leaving your Odoo environment
        * Time Savings: Eliminate manual data entry and reduce switching between different carrier portals
        * Cost Control: Compare carrier rates and monitor shipping expenses in real-time
        * Enhanced Visibility: Track all shipments and documents in one central location
        * Improved Customer Service: Provide accurate delivery estimates and real-time tracking information
        * Environmental Awareness: Monitor and report on shipment carbon footprint

        Technical Support
        ---------------

        * Dedicated technical support team (support@cargoson.com)
        * Implementation assistance with demo and go-live sessions via Teams
        * Regular updates and maintenance
        * Comprehensive documentation and training materials
        * Free carrier integration requests

        Carrier Network
        --------------

        Integrated Carrier Network
        Our extensive carrier network includes direct integrations with:

        International Carriers:
        * Schenker
        * DSV Road
        * DHL Freight
        * DHL Express
        * DHL Paket
        * Deutsche Post
        * Kuehne+Nagel
        * Rhenus Logistics
        * FedEx
        * UPS
        * TNT
        * GLS
        * DPD
        * Geodis
        * Dachser
        * Raben
        * Girteka
        * BRT (Bartolini)

        Regional Specialists:
        * ACE Logistics
        * DLW
        * Matkahuolto
        * Kaukokiito
        * Röhlig Suus
        * Bring
        * Emons
        * Delamode
        * Venipak
        * NTG
        * Speedy
        * Econt

        Postal & Last-Mile Services:
        * Posti Jakelu
        * Itella
        * Omniva
        * Postnord
        * Bolt
        * Wolt

        And 1,500+ more carriers available. Need a specific carrier? Contact support@cargoson.com for free integration setup. Missing a carrier? New integrations are included in your Cargoson plan, we'll integrate them for free.

        Getting Started
        --------------

        1. Install the connector and configure your Cargoson account credentials
        2. Add your carriers and upload your negotiated pricelists
        3. Start managing shipments directly from your Odoo interface

        For more information and support, visit: https://www.erppartner.ee/cargoson and https://www.cargoson.com
    """,
    'category': 'Inventory/Delivery',
    'sequence': 1,
    'version': '19.0.0.18',
    'application': False,
    'depends': ['stock_delivery', 'sale', 'stock', 'delivery', 'purchase'],
    'data': [
        'security/ir.model.access.csv',
        'data/data.xml',
        'views/res_config_settings_views.xml',
        'views/delivery_carrier_views.xml',
        'views/cargoson_courier_views.xml',
        'views/sale_view.xml',
        'views/product_view.xml',
        'wizard/carrier_rates.xml',
        'views/stock_picking.xml',
        'views/choose_delivery_carrier_views.xml',
        'views/replan_delivery_carrier_views.xml'
    ],
    'images': [
        'static/description/banner.gif',
    ],
    'license': 'OPL-1',
    'author': 'ERP Partner OÜ',
    'website': 'https://www.erppartner.ee',
}
