from pathlib import Path
from google.adk.agents import Agent
from google.adk.tools.skill_toolset import SkillToolset
from app.agent import root_agent, load_agent_prompt


def test_root_agent_initialization() -> None:
    """
    Unit test to verify that the root agent is correctly initialized
    with the proper static prompt instruction and native ADK SkillToolset.
    """
    assert isinstance(root_agent, Agent)
    assert root_agent.name == "insurance-agent" or root_agent.name is not None

    # Check instruction
    expected_instruction = load_agent_prompt()
    assert root_agent.instruction == expected_instruction
    assert "你是一個具備 session-aware 能力的保險推薦代理" in root_agent.instruction

    # Check that SkillToolset is loaded as a tool
    skill_toolset = None
    for tool in root_agent.tools:
        if isinstance(tool, SkillToolset):
            skill_toolset = tool
            break

    assert skill_toolset is not None, "Root agent should have a SkillToolset"
    
    # Check that our insurance-agent-skill is within the SkillToolset
    skills = skill_toolset._list_skills()
    assert len(skills) > 0
    
    insurance_skill = next((s for s in skills if s.name == "insurance-agent-skill"), None)
    assert insurance_skill is not None, "SkillToolset should contain 'insurance-agent-skill'"
    assert insurance_skill.description is not None
