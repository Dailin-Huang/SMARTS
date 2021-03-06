import math
import os
import pytest
import numpy as np

import gym


from smarts.core.agent_interface import AgentInterface, AgentType
from smarts.core.agent import AgentSpec, AgentPolicy
from smarts.core.controllers import LaneFollowingController

import smarts.sstudio.types as t
from smarts.core.tests.helpers.scenario import temp_scenario
from smarts.core.scenario import Scenario
from smarts.core.smarts import SMARTS
from smarts.core.sumo_traffic_simulation import SumoTrafficSimulation
from smarts.sstudio import gen_scenario


AGENT_ID = "Agent-007"

# Tests are parameterized based on lane following agent types
# and speeds. Note that for "Laner" type the speed is fixed.
@pytest.fixture(
    params=[
        ("keep_lane", AgentType.Laner),
        ((10, 0), AgentType.LanerWithSpeed),
        ((18, 0), AgentType.LanerWithSpeed),
    ]
)
def policy_and_agent_type(request):
    class Policy(AgentPolicy):
        def __init__(self, action=request.param[0]):
            self.action = action

        def act(self, obs):
            return self.action

    return (Policy, request.param[1])


@pytest.fixture(
    params=[
        ("maps/turning_radius/mapzshape.net.xml", "edge-east-SE"),
        ("maps/turning_radius/map55.net.xml", "edge-south-NS"),
        ("maps/turning_radius/map78.net.xml", "edge-south-NS"),
        ("maps/turning_radius/map90.net.xml", "edge-south-NS"),
        ("maps/turning_radius/map107.net.xml", "edge-south-NS"),
        ("maps/turning_radius/map128.net.xml", "edge-south-NS"),
    ]
)
def scenarios(request):
    with temp_scenario(name="map", map=request.param[0]) as scenario_root:
        mission = t.Mission(
            route=t.Route(begin=("edge-west-WE", 0, 10), end=(request.param[1], 0, 40))
        )
        gen_scenario(
            t.Scenario(ego_missions=[mission]), output_dir=scenario_root,
        )
        yield Scenario.variations_for_all_scenario_roots(
            [str(scenario_root)], [AGENT_ID]
        )


@pytest.fixture
def agent_spec(policy_and_agent_type):
    return AgentSpec(
        interface=AgentInterface.from_type(
            policy_and_agent_type[1], max_episode_steps=5000
        ),
        policy_builder=policy_and_agent_type[0],
    )


@pytest.fixture
def smarts(agent_spec):
    smarts = SMARTS(
        agent_interfaces={AGENT_ID: agent_spec.interface},
        traffic_sim=SumoTrafficSimulation(headless=True),
    )
    yield smarts
    smarts.destroy()


def test_lane_following_controller(smarts, agent_spec, scenarios):
    # We introduce a flag `detected` to find the first instance of time for which the
    # speed exceeds 5 km/hr and then we start to record the speed. If after, flag
    # `detected` becomes `True`, any element of speed becomes less than 5 km/hr then
    # the test will fail.
    detected = 0
    lateral_error = []
    speed = []

    agent = agent_spec.build_agent()
    scenario = next(scenarios)
    observations = smarts.reset(scenario)

    for _ in range(500):
        agent_obs = observations[AGENT_ID]
        agent_obs = agent_spec.observation_adapter(agent_obs)

        if agent_obs.ego_vehicle_state.speed > 5 / 3.6:
            detected = 1
        if detected == 1:
            speed.append(agent_obs.ego_vehicle_state.speed)

        current_lane = LaneFollowingController.find_current_lane(
            agent_obs.waypoint_paths, agent_obs.ego_vehicle_state.position
        )
        wp_path = agent_obs.waypoint_paths[current_lane]
        lateral_error.append(
            abs(
                wp_path[0].signed_lateral_error(
                    agent_obs.ego_vehicle_state.position[0:2]
                )
            )
        )

        agent_action = agent_spec.action_adapter(agent.act(agent_obs))
        observations, _, dones, _ = smarts.step({AGENT_ID: agent_action})

        if agent_obs.events.reached_goal:
            break

    assert agent_obs.events.reached_goal, "Didn't reach goal"
    assert min(speed) > 5 / 3.6, "Speed dropped below minimum (5)"
    assert sum(speed) / len(speed) > 5, "Average speed below maximum (5)"
    assert max(lateral_error) < 2.01, "Lateral error exceeded maximum (2)"
    assert (
        sum(lateral_error) / len(lateral_error) < 1
    ), "Average lateral error exceeded maximum (1)"
