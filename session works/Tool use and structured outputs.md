Tool 1: Search Company Knowledge (READ)
{
  "name": "search_company_knowledge",
  "description": "Search company documents and internal knowledge to answer questions about company policies, regulations, HR information and business procedures.",
  "type": "read",
  "parameters": {
    "type": "object",
    "properties": {
      "query": {
        "type": "string",
        "description": "The question or information the user wants to find."
      },
      "document_category": {
        "type": "string",
        "enum": [
          "HR",
          "Policies",
          "Regulations",
          "FAQs",
          "General"
        ],
        "description": "The category of documents to search."
      }
    },
    "required": ["query"]
  }
}
Tool 2: Get Subscription Usage (READ)
{
  "name": "get_subscription_usage",
  "description": "Retrieve subscription details, costs and usage statistics to help businesses monitor their software subscriptions.",
  "type": "read",
  "parameters": {
    "type": "object",
    "properties": {
      "subscription_name": {
        "type": "string",
        "description": "Name of the subscription to investigate."
      },
      "time_period": {
        "type": "string",
        "enum": [
          "monthly",
          "yearly"
        ],
        "description": "The period for which subscription usage is requested."
      }
    },
    "required": ["time_period"]
  }
}
Tool 3: Generate Business Report (WRITE)
{
  "name": "generate_business_report",
  "description": "Generate and save a business report using financial information and project updates for a selected reporting period.",
  "type": "write",
  "parameters": {
    "type": "object",
    "properties": {
      "report_type": {
        "type": "string",
        "enum": [
          "financial",
          "project",
          "business_performance"
        ],
        "description": "The type of business report to generate."
      },
      "time_period": {
        "type": "string",
        "enum": [
          "weekly",
          "monthly",
          "yearly"
        ],
        "description": "The reporting period."
      },
      "include_recommendations": {
        "type": "boolean",
        "description": "Whether to include AI-generated recommendations in the report."
      }
    },
    "required": ["report_type", "time_period"]
  }
}