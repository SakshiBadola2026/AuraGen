
# ============================================================
# 1. IMPORT REQUIRED LIBRARIES
# ============================================================

import os

# The heavy AI dependencies are imported lazily so the module can be imported
# in tests or other tooling without triggering a full LangChain startup.


# ============================================================
# 2. LOAD ENVIRONMENT VARIABLES
# ============================================================

def load_environment() -> None:
    """Load the project environment file regardless of the current working directory."""
    from dotenv import load_dotenv

    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "environment.env")
    load_dotenv(env_path)


# ============================================================
# 3. INITIALIZE THE GEMINI MODEL
# ============================================================

def create_llm():
    """Create the Gemini model configured for consistent responses."""
    from langchain_google_genai import ChatGoogleGenerativeAI

    return ChatGoogleGenerativeAI(
        model="gemini-2.5-flash",
        # Selects Gemini 2.5 Flash as the AI model.

        temperature=0
        # Controls response randomness.
        # 0 encourages more consistent responses.
    )


# ============================================================
# 4. CREATE A REUSABLE PROMPT TEMPLATE
# ============================================================

def create_prompt():
    """Create the template used to generate a website for a business type."""
    from langchain_core.prompts import PromptTemplate

    return PromptTemplate.from_template(
        """
        You are an expert web developer.

        Create a website for a {business_type}.

        Requirements:
        - Use HTML and CSS.
        - Create a modern and attractive design.
        - Make the website responsive for mobile and desktop.
        - Include a navigation bar.
        - Include a hero section.
        - Include a services section.
        - Include a footer.
        - Return complete HTML and CSS code.
        - Do not include explanations.
        """
    )


def create_ui_prompt():
    """Create the prompt for AuraGen's restricted, pre-styled React library."""
    from langchain_core.prompts import PromptTemplate

    return PromptTemplate.from_template(
        """
        You create small, polished financial interfaces for AuraGen.
        The user describes the interface they need:
        {user_prompt}

        Use only these pre-styled React components and quoted-string props:
        - UiPage title
        - UiCard variant="plain|accent|outlined"
        - UiHeading level="1|2|3"
        - UiText tone="default|muted|accent"
        - UiMetric label value
        - UiBadge tone="neutral|positive|accent" label
        - UiButton label action="show-message"
        - UiList and UiListItem

        Return exactly one static JSX module in this shape:
        export default function GeneratedUI() {{
          return (
            <UiPage title="A clear page title">
              <UiCard variant="accent">
                <UiHeading level="2">A useful heading</UiHeading>
                <UiText tone="default">Short, helpful copy.</UiText>
                <UiButton label="Explore" action="show-message" />
              </UiCard>
            </UiPage>
          );
        }}

        Safety and formatting rules:
        - Use only the listed Ui components and their listed properties.
        - Use quoted static text and props only. Do not use expressions, variables,
          imports, hooks, event handlers, URLs, HTML tags, styles, or JavaScript logic.
        - Do not include markdown fences or explanations.
        - Keep the source under 12000 characters and the component tree under 80 nodes.
        """
    )


def generate_ui(user_prompt: str) -> str:
    """Generate a static JSX component for the approved AuraGen component library."""
    if not user_prompt or len(user_prompt) > 500:
        raise ValueError("The UI request must contain 1 to 500 characters.")

    load_environment()
    chain = create_ui_prompt() | create_llm()
    response = chain.invoke({"user_prompt": user_prompt})
    content = response.content
    if not isinstance(content, str):
        raise TypeError("The language model returned a non-text UI response.")

    source = content.strip()
    if source.startswith("```"):
        lines = source.splitlines()
        if len(lines) < 3 or not lines[-1].startswith("```"):
            raise ValueError("The language model returned an incomplete code fence.")
        source = "\n".join(lines[1:-1]).strip()
    return source


# {business_type} is a dynamic variable.
# We can replace it with Restaurant, Hospital, Portfolio, etc.


# ============================================================
# 5. PROVIDE INPUT AND EXECUTE THE CHAIN
# ============================================================

def generate_website(business_type: str) -> str:
    """Generate a complete HTML/CSS website for the requested business type."""
    load_environment()
    llm = create_llm()
    prompt = create_prompt()
    chain = prompt | llm

    response = chain.invoke({"business_type": business_type})
    return response.content


# ============================================================
# 6. EXECUTE THE SCRIPT WHEN RUN DIRECTLY
# ============================================================

def main() -> None:
    """Generate a restaurant website when the script is run as a program."""
    print(generate_website("Restaurant"))


if __name__ == "__main__":
    main()
