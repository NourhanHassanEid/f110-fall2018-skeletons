#!/usr/bin/env python
import rospy
import os
import numpy as np
import random
import roslaunch
import jinja2
from std_msgs.msg import Bool
from gazebo_msgs.srv import DeleteModel
from racecar_rl_environments.msg import areWeDone
from racecar_rl_environments.srv import states, statesRequest, statesResponse, reward, rewardRequest, rewardResponse, startSim, startSimRequest, startSimResponse, resetSim, resetSimRequest, resetSimResponse
from racecar_communication.msg import ID, IDsCombined
from nav_msgs.msg import Odometry


class ClearEVRouteBasicEnv: #currently only handles single agent + ambulance

    def __init__(self):

        rospy.Service('states', states, self.statesServerCallback)
        rospy.Service('reward', reward, self.rewardServerCallback)
        rospy.Service('startSim', startSim, self.startSimCallback)
        rospy.Service('resetSim', resetSim, self.resetSimCallback)

        rospy.Subscriber('/id_msgs', IDStamped, self.idMsgsCallback, queue_size=50)
        self.pub = rospy.Publisher('/RL/is_active_or_episode_done', areWeDone) ##awadiha f start

        self.last_vehicle_ids = self.initIdsCombinedMsg()
        self.EV_index = -1

        self.num_of_agents = -1
        self.num_of_EVs = -1

        self.is_episode_done = 0 #int: whether the episode is finished (and the reason if it is) #TODO: better comment
        self.is_activated = False

        self.episode_start_time = -1
        self.optimal_time = 10000 #init
        self.max_time_steps = 20 * self.optimal_time #init

        self.getParams()

        self.initLaunchFiles()


    def getParams(self):

        ####
        if rospy.has_param('r_name'):
            self.r_name = rospy.get_param('r_name')
        else:
            self.r_name = "racecar"
        ####
        if rospy.has_param('reward/max_final_reward'):
            self.max_final_reward = rospy.get_param('reward/max_final_reward')
        else:
            self.max_final_reward = 20 #reward for achieving end of simulation (done) with number_of_time_steps = self.optimal time
        ####
        if rospy.has_param('reward/min_final_reward'):
            self.min_final_reward = rospy.get_param('reward/min_final_reward')
        else:
            self.min_final_reward = -20 #reward for achieving end of simulation (done) with number_of_time_steps = 20 * self.optimal time
        ####
        if rospy.has_param('reward/max_step_reward'):
            self.max_step_reward = rospy.get_param('reward/max_step_reward')
        else:
            self.max_step_reward = 0 #reward for having an acceleration of value = self.emer_max_accel over last step
        ####
        if rospy.has_param('reward/min_step_reward'):
            self.min_step_reward = rospy.get_param('reward/min_step_reward')
        else:
            self.min_step_reward = -1.25 #reward for having an acceleration of value = - self.emer_max_accel over last step
        ####
        if rospy.has_param('reward/give_final_reward'):
            self.give_final_reward = rospy.get_param('reward/give_final_reward')
        else:
            self.give_final_reward = False #whether to give a final reward or not
        ####
        if rospy.has_param('communicaion_range'):
            self.comm_range = rospy.get_param('communicaion_range')
        else:
            self.comm_range = 24
        ####
        if rospy.has_param('ambulance/amb_start_x'):
            self.amb_start_x = rospy.get_param('ambulance/amb_start_x')
        else:
            self.amb_start_x = -50
        ####
        if rospy.has_param('ambulance/amb_goal_x'):
            self.amb_goal_x = rospy.get_param('ambulance/amb_goal_x')
        else:
            self.amb_goal_x = 50
        ####
        if rospy.has_param('ambulance/amb_max_vel'):
            self.emer_max_speed = rospy.get_param('ambulance/amb_max_vel')
        else:
            self.emer_max_speed = 1
        ####
        if rospy.has_param('ambulance/amb_max_acc'):
            self.emer_max_accel = rospy.get_param('ambulance/amb_max_acc')
        else:
            self.emer_max_accel = 0.0333
        ####
        if rospy.has_param('ambulance/rel_amb_y_min'):
            if (rospy.get_param('ambulance/rel_amb_y_min') < 0):
                self.rel_amb_y_min = - min(self.comm_range, abs(rospy.get_param('ambulance/rel_amb_y_min')))
            else
                self.rel_amb_y_min = min(self.comm_range, abs(rospy.get_param('ambulance/rel_amb_y_min')))
        else:
            self.rel_amb_y_min = - min(self.comm_range, 24)
        ####
        if rospy.has_param('ambulance/rel_amb_y_max'):
            if (rospy.get_param('ambulance/rel_amb_y_max') < 0):
                self.rel_amb_y_min = - min(self.comm_range, abs(rospy.get_param('ambulance/rel_amb_y_max')))
            else
                self.rel_amb_y_min = min(self.comm_range, abs(rospy.get_param('ambulance/rel_amb_y_max')))
        else:
            self.rel_amb_y_min = min(self.comm_range, 9)

    def initLaunchFiles(self):
        self.uuid = roslaunch.rlutil.get_or_generate_uuid(None, False)
        roslaunch.configure_logging(self.uuid)

        temp_folder = os.path.join(os.path.dirname(os.path.dirname(__file__)),"templates")
        temp_loader = jinja2.FileSystemLoader(searchpath = temp_folder)
        temp_env = jinja2.Environment(loader=temp_loader)

        ####
        empty_world_temp = temp_env.get_template("empty_world_temp.launch")

        self.empty_world = ['racecar_gazebo', 'empty_world.launch']
        self.empty_world = roslaunch.rlutil.resolve_launch_arguments(self.empty_world)
        empty_world_args = dict()

        if rospy.has_param('gazebo_gui'):
            empty_world_args['gui'] = rospy.get_param('gazebo_gui')
        else:
            empty_world_args['gui'] = False

        with open(self.empty_world[0], "w") as fp:
            fp.writelines(empty_world_temp.render(data=empty_world_args))

        ####
        self.rviz = ['racecar_rviz', 'view_two_navigation.launch']
        self.rviz = roslaunch.rlutil.resolve_launch_arguments(self.rviz)

        ####
        self.one_racecar_one_ambulance_temp = temp_env.get_template("one_racecar_one_ambulance_temp.launch")

        self.one_racecar_one_ambulance = ['racecar_clear_ev_route', 'one_racecar_one_ambulance.launch']
        self.one_racecar_one_ambulance = roslaunch.rlutil.resolve_launch_arguments(self.one_racecar_one_ambulance)


    # Initialize IDsCombined message 
    def initIdsCombinedMsg(self):
        idsCombined_msg = IDsCombined()

	# Set the initialization value of lane number to -1 (flag)
        for i in range(0,len(idsCombined_msg.ids),1):
            idsCombined_msg.ids[i].lane_num = -1

        return idsCombined_msg


    def startSimCallback(self, req):

        resp = statesResponse()

        if (req.num_of_agents != 1):
            raise Exception("Current version only supports ONE SINGLE AGENT. Terminating.")
            resp.is_successful = False
            return resp

        if (req.num_of_EVs != 1):
            raise Exception("Current version only supports ONE EMERGENCY VEHICLE. Terminating.")
            resp.is_successful = False
            return resp

        self.num_of_agents = req.num_of_agents
        self.num_of_EVs = req.num_of_EVs
        self.is_episode_done = 0
        self.is_activated = False

        self.launch_empty_world = roslaunch.parent.ROSLaunchParent(self.uuid, self.empty_world, force_screen=True, verbose=True)
        self.launch_empty_world.start()
        rospy.sleep(5)

        ####
        one_racecar_one_ambulance_args = self.genTemplateArgs()

        with open(self.one_racecar_one_ambulance[0], "w") as fp:
            fp.writelines(self.one_racecar_one_ambulance_temp.render(data=one_racecar_one_ambulance_args))

        self.launch_one_racecar_one_ambulance = roslaunch.parent.ROSLaunchParent(self.uuid, self.one_racecar_one_ambulance, force_screen=True, verbose=True)
        self.launch_one_racecar_one_ambulance.start()
        rospy.sleep(5)

        ####
        self.launch_rviz = roslaunch.parent.ROSLaunchParent(self.uuid, self.rviz, force_screen=True, verbose=True)
        self.launch_rviz.start()

        rospy.sleep(60)

        self.episode_start_time = rospy.Time.now()

        resp.is_successful = True
        return resp

    
    def resetSimCallback(self, req):

        resp = statesResponse()

        if (req.reset_env == True):

            delete_model_client = rospy.ServiceProxy('gazebo/delete_model', DeleteModel)
            rospy.wait_for_service('gazebo/delete_model')

            for agent_index in range((self.num_of_agents + self.num_of_EVs)):
                delete_model_client(self.r_name + str(agent_index + 1))
            rospy.sleep(20)

            self.launch_one_racecar_one_ambulance.shutdown()
            rospy.sleep(60)

            self.last_vehicle_ids = self.initIdsCombinedMsg()
            self.is_episode_done = 0
            self.is_activated = False

            ####
            one_racecar_one_ambulance_args = self.genTemplateArgs()

            with open(self.one_racecar_one_ambulance[0], "w") as fp:
                fp.writelines(self.one_racecar_one_ambulance_temp.render(data=one_racecar_one_ambulance_args))

            self.launch_one_racecar_one_ambulance = roslaunch.parent.ROSLaunchParent(self.uuid, self.one_racecar_one_ambulance, force_screen=True, verbose=True)
            self.launch_one_racecar_one_ambulance.start()
            rospy.sleep(60)

            self.episode_start_time = rospy.Time.now()

            resp.is_successful = True
            return resp

        else:
            resp.is_successful = False
            return resp

    def genTemplateArgs(self):

        one_racecar_one_ambulance_args = dict()

        # agent args
        #amb_max_vel_coord = self.amb_start_x + ((self.emer_max_speed*self.emer_max_speed)/(2*self.emer_max_accel)) #TODO

        one_racecar_one_ambulance_args['agent_start_x'] = random.randint(self.amb_start_x - self.rel_amb_y_min, ((self.amb_goal_x - self.amb_start_x)/2))
        one_racecar_one_ambulance_args['agent_start_y'] = self.genRandLanePos()

        if rospy.has_param('agent/agent_max_vel'):
            agent_max_vel = rospy.get_param('agent/agent_max_vel')
        else:
            agent_max_vel = 0.5

        if rospy.has_param('agent/agent_max_acc'):
            agent_max_acc = rospy.get_param('agent/agent_max_acc')
        else:
            agent_max_acc = 0.0167

        one_racecar_one_ambulance_args['agent_max_vel'] = agent_max_vel
        one_racecar_one_ambulance_args['agent_max_acc'] = agent_max_acc

        one_racecar_one_ambulance_args['agent_goal_x'] = self.amb_goal_x
        one_racecar_one_ambulance_args['agent_goal_y'] = one_racecar_one_ambulance_args['agent_start_y']

        # EV args
        one_racecar_one_ambulance_args['EV_start_x'] = self.amb_start_x
        one_racecar_one_ambulance_args['EV_start_y'] = self.genRandLanePos()
        one_racecar_one_ambulance_args['EV_max_vel'] = self.emer_max_speed
        one_racecar_one_ambulance_args['EV_max_acc'] = self.emer_max_accel

        # communication range
        one_racecar_one_ambulance_args['comm_range'] = self.self.comm_range

        return one_racecar_one_ambulance_args

    def genRandLanePos(self):
        '''  
         Lane number convention in the "threeLanes" world:
             y^  
              |--------------> x      lane number 0 
              |--------------> x      lane number 1
              |--------------> x      lane number 2 
        '''

        lane_num = random.randint(0, 2)

        if (lane_num == 0):
           return 0.2625
        elif (lane_num == 1):
           return -0.2625
        if (lane_num == 2):
           return -0.7875


    def idMsgsCallback(self, idMsgs):
 
        # extra check
        if (idMsgs.robot_num > (self.num_of_agents + self.num_of_EVs)):
            raise Exception("NUMBER OF VEHICLES IN ENVIRONMENT IS GREATER THAN EXPECTED!")

        self.last_vehicle_ids.header.stamp = rospy.Time.now()
        self.last_vehicle_ids.header.frame_id = "map"
        self.last_vehicle_ids.robot_num = idMsgs.robot_num

        self.last_vehicle_ids.ids[idMsgs.robot_num - 1] = idMsgs.id

        if (idMsgs.id.type.data == "ambulance"):
            if (self.EV_index == -1):
                self.EV_index = idMsgs.robot_num - 1
                self.emer_max_speed = idMsgs.id.max_vel
                self.emer_max_accel = idMsgs.id.max_acc
                self.optimal_time = int(np.round((self.amb_goal_x - self.amb_start_x) / self.emer_max_speed))  # Optimal number of time steps: number of time steps taken by ambulance at maximum speed
                self.max_time_steps = 20 * self.optimal_time
            # extra check
            elif (self.EV_index != (idMsgs.robot_num - 1)):
                raise Exception("NUMBER OF EMERGENCY VEHICLES IN ENVIRONMENT IS GREATER THAN EXPECTED!")


        self.are_we_done()
        self.is_RL_activated()

        msg = areWeDone()
        msg.is_activated = self.is_activated
        msg.is_episode_done = self.is_episode_done
        self.pub.publish(msg)


    def is_RL_activated(self):

        for agent_index in range((self.num_of_agents + self.num_of_EVs)):
            if (agent_index != self.EV_index):

                agent_x_pos = self.last_vehicle_ids.ids[agent_index].x_position
                amb_x_pos = self.last_vehicle_ids.ids[self.EV_index].x_position
                rel_amb_x = amb_x_pos - agent_x_pos

                if ((rel_amb_x < self.rel_amb_y_min) or (rel_amb_x > self.rel_amb_y_max)): # misleading name TODO: change to rel_amb_x_min 
                    self.is_activated = False
                else:
                    self.is_activated = True
        return


    def are_we_done(self):

        amb_abs_y = self.last_vehicle_ids.ids[self.EV_index].x_position # misleading name TODO: change to amb_abs_x 

        #1: steps == max_time_steps - 1
        time_step_number = rospy.Time.now() - self.episode_start_time
        if(time_step_number == self.max_time_steps-1):
            self.is_episode_done = 1
            return
        #2: goal reached
        elif(amb_abs_y > self.amb_goal_x - self.emer_max_speed - 1):
            # DONE: Change NET file to have total distance = 511. Then we can have the condition to compare with 500 directly.
            #return 2 #GOAL IS NOW 500-10-1 = 489 cells ahead. To avoid ambulance car eacaping
            self.is_episode_done = 2
            return 
        for agent_index in range((self.num_of_agents + self.num_of_EVs)):
            if (agent_index != self.EV_index):
                agent_abs_y = self.last_vehicle_ids.ids[agent_index].x_position # misleading name TODO: change to agent_abs_x

                if (agent_abs_y > self.amb_goal_x - self.emer_max_speed - 1):
                    self.is_episode_done = 3
                    return

        # 0: not done
        self.is_episode_done = 0
        return


    def calc_reward(self, amb_last_velocity, execution_time): #TODO TODO: edit reward to use execution time

        '''
        :logic: Calculate reward to agent from current state
        :param amb_last_velocity: float, previous velocity the EV/ambulance had
        :return: reward (either step reward or final reward)


        :Notes:
        #Simulation Time is not allowed to continue after 20*optimal_time (20* time steps with ambulance at its maximum speed)
        '''

        if(self.is_episode_done and self.give_final_reward): #Calculate a final reward

            number_of_time_steps = rospy.Time.now() - self.episode_start_time #Time spent in episode so far

            #Linear reward. y= mx +c. y: reward, x: ration between time achieved and optimal time. m: slope. c: y-intercept
            m = ( (self.max_final_reward - self.min_final_reward) *20 ) /19 #Slope for straight line equation to calculate final reward
            c = self.max_final_reward - 1*m #c is y-intercept for the reward function equation #max_final_reward is the y for x = 1
            reward = m * (self.optimal_time/number_of_time_steps) + c
            #debug#print(f'c: {c}, m: {m}, steps: {number_of_time_steps}, optimal_time: {self.optimal_time}')
            return reward

        else: #Calcualate a step reward
            steps_needed_to_halt = 30
            ration_of_halt_steps_to_total_steps = steps_needed_to_halt/(self.amb_goal_x - self.amb_start_x)

            m = (self.max_step_reward - self.min_step_reward)/(2 * self.emer_max_accel)  # Slope for straight line equation to calculate step reward
            # debug # rospy.loginfo("M = : %f", m)
            #2 * self.emer.max_accel since: = self.emer.max_accel - * self.emer.max_decel
            c = self.max_step_reward - self.emer_max_accel * m  # c is y-intercept for the reward function equation #max_step_reward is the y for x = 2 (max acceleration)
            # debug # rospy.loginfo("C = : %f", c)
            emer_curr_spd = self.last_vehicle_ids.ids[self.EV_index].velocity
            reward = m * (emer_curr_spd - amb_last_velocity) + c
            rospy.loginfo("Reward = : %f", reward)
            #debug#print(f'c: {c}, m: {m}, accel: {(self.emer.spd - amb_last_velocity)}')
            #rospy.loginfo("if condition = : %f", abs(amb_last_velocity-self.emer_max_speed))
            
            if ( abs(amb_last_velocity-self.emer_max_speed) <= 1e-10 ):
            #since ambulance had maximum speed and speed did not change that much; unless we applied the code below.. the acceleration
            #   will be wrongly assumed to be zero. Although the ambulance probably could have accelerated more, but this is its maximum velocity.
                reward = self.max_step_reward #same reward as maximum acceleration (+2),

            rospy.loginfo("Reward after return = : %f", reward)

            return reward

    def statesServerCallback(self, req):

        resp = statesResponse()

        resp.agent_vel = self.last_vehicle_ids.ids[req.robot_num - 1].velocity
        resp.agent_lane = self.last_vehicle_ids.ids[req.robot_num - 1].lane_num

        resp.amb_vel = self.last_vehicle_ids.ids[self.EV_index].velocity
        resp.amb_lane = self.last_vehicle_ids.ids[self.EV_index].lane_num


        agent_x_pos = self.last_vehicle_ids.ids[req.robot_num - 1].x_position
        amb_x_pos = self.last_vehicle_ids.ids[self.EV_index].x_position

        resp.rel_amb_y = int(np.round(amb_x_pos - agent_x_pos)) # misleading name TODO: change to rel_amb_x 
     
        return resp

    def rewardServerCallback(self, req):
        
        myreward = self.calc_reward(req.amb_last_velocity, req.execution_time)
        return rewardResponse(myreward)


if __name__ == '__main__':
    rospy.init_node("clear_ev_route_basic_env")

    cEVrbe = ClearEVRouteBasicEnv()

    rospy.spin()