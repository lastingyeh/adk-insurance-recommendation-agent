from pathlib import Path
from google.adk.agents import Agent
from google.adk.tools import AgentTool
from google.adk.tools.skill_toolset import SkillToolset
from app.agent import root_agent, load_agent_prompt


def test_root_agent_initialization() -> None:
    """
    Unit test to verify that the root (Supervisor) agent and its A2A sub-agents
    are correctly initialized.
    """
    assert isinstance(root_agent, Agent)
    assert root_agent.name == "insurance-agent" or root_agent.name is not None

    # Check Supervisor instruction
    expected_instruction = load_agent_prompt()
    assert root_agent.instruction == expected_instruction
    assert "你是一個企業級保險推薦系統的監督路由器（Supervisor Agent）" in root_agent.instruction

    # Check A2A sub-agents wrapped as AgentTools
    rec_agent_tool = None
    faq_agent_tool = None
    claim_agent_tool = None
    underwriting_agent_tool = None

    for tool in root_agent.tools:
        if isinstance(tool, AgentTool):
            sub_agent = tool.agent
            if sub_agent.name == "recommendation_agent":
                rec_agent_tool = tool
            elif sub_agent.name == "faq_agent":
                faq_agent_tool = tool
            elif sub_agent.name == "claim_agent":
                claim_agent_tool = tool
            elif sub_agent.name == "underwriting_agent":
                underwriting_agent_tool = tool

    assert rec_agent_tool is not None, "Supervisor should have 'recommendation_agent' AgentTool"
    assert faq_agent_tool is not None, "Supervisor should have 'faq_agent' AgentTool"
    assert claim_agent_tool is not None, "Supervisor should have 'claim_agent' AgentTool"
    assert underwriting_agent_tool is not None, "Supervisor should have 'underwriting_agent' AgentTool"

    # Verify that RecommendationAgent has the SkillToolset
    rec_agent = rec_agent_tool.agent
    skill_toolset = None
    for tool in rec_agent.tools:
        if isinstance(tool, SkillToolset):
            skill_toolset = tool
            break

    assert skill_toolset is not None, "RecommendationAgent should have a SkillToolset"
    
    # Check that our insurance-agent-skill is within the SkillToolset
    skills = skill_toolset._list_skills()
    assert len(skills) > 0
    
    insurance_skill = next((s for s in skills if s.name == "insurance-agent-skill"), None)
    assert insurance_skill is not None, "SkillToolset should contain 'insurance-agent-skill'"
    assert insurance_skill.description is not None
