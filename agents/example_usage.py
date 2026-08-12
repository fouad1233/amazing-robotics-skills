"""
Example usage of the enhanced visualization system
"""

from src.roboagents.visualizer import visualizer, terminal_status_display
from src.roboagents.experts.test_agent import TestAgent

async def demo_visualization():
    """Demonstrate how visualization would work in practice"""

    print("🚀 Starting roboagents visualization demo")

    # Enable visualization
    visualizer.enable()

    # Register some agents
    visualizer.add_agent("IsaacSimAgent", "simulator")
    visualizer.add_agent("NewtonPhysicsAgent", "physics")
    visualizer.add_agent("USDAgent", "data")

    # Update statuses
    visualizer.update_agent_status("IsaacSimAgent", "working", "Initializing simulation...")
    visualizer.update_agent_status("NewtonPhysicsAgent", "completed", "Physics setup done")
    visualizer.update_agent_status("USDAgent", "idle", "Ready for data processing")

    # Show terminal status
    terminal_status_display()

    print("\n📋 Agent statuses:")
    statuses = visualizer.get_all_statuses()
    for name, status in statuses.items():
        print(f"  {name}: {status.status} - {status.activity}")

    print("\n✅ Demo completed successfully!")

if __name__ == "__main__":
    import asyncio
    asyncio.run(demo_visualization())