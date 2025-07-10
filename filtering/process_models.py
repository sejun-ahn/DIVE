import torch
import scipy.constants
from pymlg.torch import SO3
from pymlg.torch import SE23
from pymlg.torch import utils

from .filtering_utils import form_time_machine, form_N_matrix


class PreintegratedProcessModel:
    """
    Preintegrated Kinematics Model with incremental jacobians in SE_2(3)
    """

    def __init__(self, Q_c : torch.Tensor, perturbation = "right", g_a=torch.tensor([[0], [0], [-scipy.constants.g]])):
        
        # continuous-time IMU covariance matrix
        self.Q_c = Q_c

        # gravity vector (defaults to positive-upwards frame)
        self.g_a = g_a.reshape(1, 3, 1)

        # perturbation, default right
        self.perturbation = perturbation

        if (perturbation != "right") and (perturbation != "left"):
            raise ValueError("perturbation must be either 'right' or 'left'")

        # incremental matricies for preintegrated process model
        self.U_ij = torch.eye(5, 5).unsqueeze(0)
        self.G_ij = torch.eye(5, 5).unsqueeze(0)
        self.B_ij = torch.zeros(9, 6).unsqueeze(0)
        self.Q_ij = torch.zeros(15, 15).unsqueeze(0)
        self.P_i = torch.zeros(15, 15).unsqueeze(0)
        self.P_j = torch.zeros(15, 15).unsqueeze(0)

        # complete state jacobian
        self.A_ij = torch.zeros(15, 15).unsqueeze(0)

        # complete noise jacobian
        self.L_ij = torch.zeros(15, 12).unsqueeze(0)

    def evaluate(self, x, u, dt):
        """
        Implementation of f(x) for SE_2(3) process model. Propogates state x_{k-1} to x_{k}, and accumulates
        incremental matricies to allow for computationally efficient jacobian calculation

        Parameters
        ----------
        x : torch.Tensor
            The previous state. Expected to be a tensor with shape [N, 5, 5]
        u : torch.Tensor
            The input value. Expected to be a tensor with shape [N, 2, 3]
        dt : float
            The time interval :math:`\Delta t` between the two states, as a float

        Returns
        -------
        x_{k} : torch.Tensor
            The current state - a torch.Tensor with identical dimensions to x_{k-1}
        """

        U = CoupledIMUKinematicModel.generate_u(u, dt)

        G = CoupledIMUKinematicModel.generate_g(u, dt, g_a = self.g_a)

        x = G @ x @ U

        # complete preintegrated process model

        self.U_ij = self.U_ij @ U
        self.G_ij = G @ self.G_ij

        A = CoupledIMUKinematicModel.ie3_adj(CoupledIMUKinematicModel.ie3_inv(U))
        L = CoupledIMUKinematicModel.input_jacobian_pose(u, dt)

        self.B_ij = A @ self.B_ij - L

        # equation (59) from Compact IMU Kinematics document
        A_full = torch.zeros(u.shape[0], 15, 15)
        A_full[:, 9:15, 9:15] = utils.batch_eye(u.shape[0], 6, 6) #torch.eye(6, 6).unsqueeze(0)
        A_full[:, 0:9, 0:9] = A
        A_full[:, 0:9, 9:15] = -L
        
        L_full = torch.zeros(u.shape[0], 15, 12)
        L_full[:, 0:9, 0:6] = L
        L_full[:, 9:15, 6:12] = dt * utils.batch_eye(u.shape[0], 6, 6)

        self.Q_ij = A_full @ self.Q_ij @ A_full.transpose(1, 2) + L_full @ (self.Q_c / dt) @ L_full.transpose(1, 2)

        if (self.perturbation == 'right'):

            # complete full state jacobian
            self.A_ij = torch.zeros(u.shape[0], 15, 15)
            self.A_ij[:, 9:15, 9:15] = utils.batch_eye(u.shape[0], 6, 6)
            self.A_ij[:, :9, :9] = CoupledIMUKinematicModel.ie3_adj(CoupledIMUKinematicModel.ie3_inv(self.U_ij))
            self.A_ij[:, :9, 9:15] = self.B_ij

            # complete full noise jacobian
            self.L_ij = utils.batch_eye(u.shape[0], 15, 15)

            # self.P_j = self.A_ij @ self.P_i @ self.A_ij.transpose(1, 2) + self.L_ij @ self.Q_ij @ self.L_ij.transpose(1, 2)

        elif (self.perturbation == 'left'):
            # complete full state jacobian
            self.A_ij = torch.zeros(u.shape[0], 15, 15)
            self.A_ij[:, 9:15, 9:15] = utils.batch_eye(u.shape[0], 6, 6)
            self.A_ij[:, :9, :9] = CoupledIMUKinematicModel.ie3_adj(self.G_ij)
            self.A_ij[:, :9, 9:15] = SE23.adjoint(x) @ self.B_ij

            # complete full noise jacobian
            self.L_ij = utils.batch_eye(u.shape[0], 15, 15)
            self.L_ij[:, :9, :9] = SE23.adjoint(x)

            # self.P_j = self.A_ij @ self.P_i @ self.A_ij.transpose(1, 2) + self.L_ij @ self.Q_ij @ self.L_ij.transpose(1, 2)

        return x
    
    def reset_incremental_jacobians(self, P):
        """
        Resets incremental jacobians for correct usage of preintegration process model

        Parameters
        ----------
        P : torch.Tensor
            The current covariance matrix. Expected to be a tensor with shape [N, 15, 15]
        """

        self.U_ij = torch.eye(5, 5).unsqueeze(0)
        self.G_ij = torch.eye(5, 5).unsqueeze(0)
        self.B_ij = torch.zeros(9, 6).unsqueeze(0)
        self.Q_ij = torch.zeros(15, 15).unsqueeze(0)

        self.P_i = P
        self.P_j = P

    def state_jacobian(self, x, u, dt):
        """
        Implementation of the process jacobian with respect to the state for the IMU process model in SE_2(3)
        
        Parameters
        ----------
        x : torch.Tensor
            The previous state. Expected to be a tensor with shape [N, 5, 5]
        u : torch.Tensor
            The input value. Expected to be a tensor with shape [N, 2, 3]
        dt : float
            The time interval :math:`\Delta t` between the two states, as a float
        
        Returns
        -------
        F_{k-1} : torch.Tensor
            The process jacobian with respect to the state : torch.Tensor with shape (N, 15, 15)
        """
        return self.A_ij

    def covariance(self, x, u, dt):
        """
        Implementation of the process covariance for the IMU process model in SE_2(3). Includes embedded process jacobians wrt. the state

        Parameters
        ----------
        x : torch.Tensor
            The previous state. Expected to be a tensor with shape [N, 5, 5]
        u : torch.Tensor
            The input value. Expected to be a tensor with shape [N, 2, 3]
        dt : float
            The time interval :math:`\Delta t` between the two states, as a float

        Returns
        -------
        Q_{k-1} : torch.Tensor
            The process covariance : torch.Tensor with shape (N, 15, 15)
        """
        L_ij = self.L_ij.double()
        Q_ij = self.Q_ij.double()

        return L_ij @ Q_ij @ L_ij.transpose(1, 2)

class CoupledIMUKinematicModel:
    """
    Generic coupled IMU process model in SE_2(3)
    """

    def __init__(self, Q_c : torch.Tensor, perturbation = "right", g_a=torch.tensor([[0], [0], [-scipy.constants.g]])):
        # continuous-time covariance matrix
        self.Q_c = Q_c

        # gravity vector (defaults to positive-upwards frame)
        self.g_a = g_a.reshape(1, 3, 1)

        # perturbation, default right
        self.perturbation = perturbation

        if (perturbation != "right") and (perturbation != "left"):
            raise ValueError("perturbation must be either 'right' or 'left'")

    @staticmethod
    def generate_u(u: torch.Tensor, dt: float):
        """
        helper function for generating the U_{k-1} propogation matrix based off the imu inputs and dt

        Parameters
        ----------
        u : torch.Tensor
            The input value : torch.Tensor with shape [N, 2, 3]
        dt : float
            The time interval :math:`\Delta t` between the two states : float

        Returns
        -------
        U_{k-1} : torch.Tensor
            Auxiliary propogation matrix : torch.Tensor with shape [N, 5, 5]
        """

        omega = u[:, 0, :]
        acc = u[:, 1, :]

        lower_block = torch.cat(
            (torch.zeros(u.shape[0], 2, 3), utils.batch_eye(u.shape[0], 2, 2)), dim=2
        )
        lower_block[:, 0, 4] = dt

        u_00 = SO3.Exp(dt * omega)
        u_01 = dt * SO3.left_jacobian(dt * omega) @ acc.unsqueeze(2)
        u_02 = (dt**2 / 2) * form_N_matrix(dt * omega) @ acc.unsqueeze(2)

        upper_block = torch.cat((u_00, u_01, u_02), dim=2)

        return torch.cat((upper_block, lower_block), dim=1)

    @staticmethod
    def generate_u_inverse(u: torch.Tensor, dt: float):

        return CoupledIMUKinematicModel.ie3_inv(CoupledIMUKinematicModel.generate_u(u, dt))

    @staticmethod
    def generate_g(u: torch.Tensor, dt: float, g_a : torch.Tensor):
        """
        helper function for generating the constant G_{k-1} propogation matrix

        Parameters
        ----------
        u : torch.Tensor
            The input value : torch.Tensor with shape [N, 2, 3]
        dt : float
            The time interval :math:`\Delta t` between the two states : float
        g_a : torch.Tensor
            The gravity vector in the defined inertial frame

        Returns
        -------
        G_{k-1} : torch.Tensor
            Auxiliary propogation matrix : torch.Tensor with shape [N, 5, 5]
        """

        # generate lower block
        lower_block = torch.cat(
            (torch.zeros(u.shape[0], 2, 3), utils.batch_eye(u.shape[0], 2, 2)), dim=2
        )
        lower_block[:, 0, 4] = -dt

        u_00 = utils.batch_eye(u.shape[0], 3, 3)
        u_01 = utils.batch_vector(u.shape[0], dt * g_a)
        u_02 = utils.batch_vector(u.shape[0], -(dt**2 / 2) * g_a)

        upper_block = torch.cat((u_00, u_01, u_02), dim=2)

        return torch.cat((upper_block, lower_block), dim=1)

    @staticmethod
    def generate_nu(u: torch.Tensor, dt: float):
        """
        helper function for generating the v_{k-1} vector in R_{9} given an IMU input. When projected into SE_2(3), this parameterization generates an
        equivalent SE_2(3) object to the series expansion of the continuous-time U (after seperation via time machine to make said object a member of
        SE_2(3))

        Parameters
        ----------
        u : torch.Tensor
            The input value : torch.Tensor with shape [N, 2, 3]
        dt : float
            The time interval :math:`\Delta t` between the two states : float

        Returns
        -------
        v_{k-1} : torch.Tensor
            A SE_2(3) parameterization given an IMU measurement : torch.Tensor with shape (N, 9)
        """

        omega = u[:, 0, :]
        acc = u[:, 1, :]

        nu_0 = dt * omega
        nu_1 = dt * acc
        nu_2 = (
            (dt**2 / 2)
            * SO3.left_jacobian_inv(dt * omega)
            @ form_N_matrix(dt * omega)
            @ acc.unsqueeze(2)
        )

        nu = torch.cat((nu_0, nu_1, nu_2.squeeze(2)), dim=1)

        return nu

    @staticmethod
    def generate_upsilon(u: torch.Tensor, dt: float):
        # check this numerically against the inputs for nu generation
        """
        helper function to generate Y_{k-1}, an approximate Jacobian of v() with respect to the IMU inputs.

        Parameters
        ----------
        u : torch.Tensor
            The input value : torch.Tensor with shape [N, 2, 3]
        dt : float
            The time interval :math:`\Delta t` between the two states : float

        Returns
        -------
        Y_{k-1} : torch.Tensor
            An approximate Jacobian of v() : torch.Tensor with shape (N, 9, 6)
        """

        omega = u[:, 0, :]
        acc = u[:, 1, :].reshape(1, 3, 1)

        Om = SO3.wedge(omega * dt)
        OmOm = Om @ Om

        # TODO: confirm that this isn't elementwise. doesn't look like it, but just to make sure
        W = (
            OmOm @ SO3.wedge(acc)
            + Om @ SO3.wedge(Om @ acc)
            + SO3.wedge(OmOm @ acc)
        )

        batch_dt = dt * utils.batch_eye(u.shape[0], 3, 3)

        upsilon_30 = (dt**3) * ((SO3.wedge(acc) / 12) - (dt**2 * W) / 720)

        # navlie definition of upsilon
        upsilon_30_navlie = (-0.5 * (dt**2 / 2) * ((1 / 360) * (dt**3) * (OmOm @ SO3.wedge(acc) + Om @ (SO3.wedge(Om @ acc)) + SO3.wedge(OmOm @ acc)) - (1 / 6) * dt * SO3.wedge(acc)))

        upsilon_31 = (
            ((dt**2) / 2)
            * SO3.left_jacobian_inv(dt * omega)
            @ form_N_matrix(dt * omega)
        )

        c0 = torch.cat((batch_dt, torch.zeros(u.shape[0], 3, 3), upsilon_30_navlie), dim=1)
        c1 = torch.cat((torch.zeros(u.shape[0], 3, 3), batch_dt, upsilon_31), dim=1)

        return torch.cat((c0, c1), dim=2)
    
    @staticmethod
    def ie3_adj(X):
        # retrieve batched matrix components
        C, v, r = SE23.to_components(X)
        
        # retrieve internal scalar
        c = X[:, 3, 4]

        ad_20 = -SO3.wedge(c.unsqueeze(1) * v - r) @ C
        ad_10 = SO3.wedge(v) @ C

        r0 = torch.cat((C, torch.zeros(C.shape[0], 3, 3), torch.zeros(C.shape[0], 3, 3)), dim=2)
        r1 = torch.cat((ad_10, C, torch.zeros(C.shape[0], 3, 3)), dim=2)
        r2 = torch.cat((ad_20, -c.reshape(-1, 1, 1) * C, C), dim=2)

        return torch.cat((r0, r1, r2), dim=1)
    
    @staticmethod
    def ie3_inv(X):
        # retrieve batch components (NOT a member of SE_2(3) but can use same to_components functions)
        C, v, r = SE23.to_components(X)

        # retrieve internal scalar
        c = X[:, 3, 4]

        lower_block = torch.cat(
            (torch.zeros(v.shape[0], 2, 3), utils.batch_eye(v.shape[0], 2, 2)), dim=2
        )
        lower_block[:, 0, 4] = -c

        u_00 = C.transpose(1, 2)
        u_01 = -C.transpose(1, 2) @ v
        u_02 = C.transpose(1, 2) @ (c * v - r)

        upper_block = torch.cat((u_00, u_01, u_02), dim=2)

        return torch.cat((upper_block, lower_block), dim=1)

    def evaluate(self, x, u, dt):
        """
        Implementation of f(x) for SE_2(3) process model. Propogates state x_{k-1} to x_{k}, and accumulates
        incremental matricies to allow for computationally efficient jacobian calculation

        Parameters
        ----------
        x : torch.Tensor
            The previous state. Expected to be a tensor with shape [N, 5, 5]
        u : torch.Tensor
            The input value. Expected to be a tensor with shape [N, 2, 3]
        dt : float
            The time interval :math:`\Delta t` between the two states, as a float

        Returns
        -------
        x_{k} : torch.Tensor
            The current state - a torch.Tensor with identical dimensions to x_{k-1}
        """

        U = self.generate_u(u, dt)

        G = self.generate_g(u, dt, g_a = self.g_a)

        x = G @ x @ U

        return x

    def state_jacobian(self, x, u, dt):
        """
        Implementation of the process jacobian with respect to the state for the IMU process model in SE_2(3)
        
        Parameters
        ----------
        x : torch.Tensor
            The previous state. Expected to be a tensor with shape [N, 5, 5]
        u : torch.Tensor
            The input value. Expected to be a tensor with shape [N, 2, 3]
        dt : float
            The time interval :math:`\Delta t` between the two states, as a float
        
        Returns
        -------
        F_{k-1} : torch.Tensor
            The process jacobian with respect to the state : torch.Tensor with shape (N, 15, 15)
        """
        if self.perturbation == "right":
            r0 = torch.cat(
                (
                    self.ie3_adj((self.generate_u_inverse(u, dt))),
                    -self.input_jacobian_pose(u, dt),
                ),
                dim=2,
            )
        else:
            r0 = torch.cat(
                (
                    self.ie3_adj((self.generate_g(u = u, dt = dt, g_a = self.g_a))),
                    -SE23.adjoint(x) @ self.input_jacobian_pose(u, dt),
                ),
                dim=2,
            )
        r1 = torch.cat(
            (torch.zeros(u.shape[0], 6, 9), utils.batch_eye(u.shape[0], 6, 6)), dim=2
        )

        return torch.cat((r0, r1), dim=1)

    @staticmethod
    def input_jacobian_pose(u, dt):
        """
        Implementation of the process jacobian with respect to the input for the IMU process model (excluding bias) in SE_2(3)

        Parameters
        ----------
        x : torch.Tensor
            The previous state. Expected to be a tensor with shape [N, 5, 5]
        u : torch.Tensor
            The input value. Expected to be a tensor with shape [N, 2, 3]
        dt : float
            The time interval :math:`\Delta t` between the two states, as a float

        Returns
        -------
        L_{k-1} : torch.Tensor
            The process jacobian (excluding bias) with respect to the input : torch.Tensor with shape (N, 6, 6)
        """
        L = SE23.left_jacobian(-CoupledIMUKinematicModel.generate_nu(u, dt)) @ CoupledIMUKinematicModel.generate_upsilon(u, dt)

        return L

    def input_jacobian(self, x, u, dt):
        if (self.perturbation == "right"):
            r0 = torch.cat(
                (self.input_jacobian_pose(u, dt), torch.zeros(u.shape[0], 9, 6)), dim=2
            )
        else:
            r0 = torch.cat(
                (SE23.adjoint(x) @ self.input_jacobian_pose(u, dt), torch.zeros(u.shape[0], 9, 6)), dim=2
            )
        r1 = torch.cat(
            (torch.zeros(u.shape[0], 6, 6), dt * utils.batch_eye(u.shape[0], 6, 6)),
            dim=2,
        )

        return torch.cat((r0, r1), dim=1)

    def covariance(self, x, u, dt):
        Q_k = (
            self.input_jacobian(x, u, dt)
            @ (self.Q_c / dt)
            @ self.input_jacobian(x, u, dt).transpose(1, 2)
        )

        return Q_k


class IMUKinematicModel:
    """
    Generic de-coupled IMU process model
    """

    def __init__(
        self,
        x,
        g_a=torch.tensor([[0], [0], [-scipy.constants.g]]),
    ):
        # initialize gravity vector
        self.g_a = g_a

        # TODO: temporary DCM in-place
        self.C = SO3.Exp_torch(x[:, 0:3])

    def evaluate(self, x: torch.Tensor, u: torch.Tensor, dt: float) -> torch.Tensor:
        """
        Implementation of f(x). Propogates state x_{k-1} to x_{k} and returns it

        Parameters
        ----------
        x : torch.Tensor
            The previous state. Expected to be a tensor with shape [N, 15, 1]
        u : torch.Tensor
            The input value. Expected to be a tensor with shape [N, 2, 3]
        dt : float
            The time interval :math:`\Delta t` between the two states, as a float

        Returns
        -------
        x_{k} : torch.Tensor
            The current state - a torch.Tensor with identical dimensions to x_{k-1}
        """

        # implementing f(x)

        # retrieve individual IMU measurements(TODO: these need to be made into a batch format resulting in [N x 3 x 1] input vectors)
        omega_k = u[:, 0]
        acc_k = u[:, 1]

        r = x[:, 6:9]
        v = x[:, 3:6]
        self.C = SO3.Exp_torch(x[:, 0:3])

        r = r + dt * v + (dt**2 / 2) * (self.g_a + self.C @ acc_k.reshape(-1, 1))

        v = v + dt * self.g_a + dt * self.C @ acc_k.reshape(-1, 1)

        self.C = self.C @ SO3.Exp_torch(dt * omega_k)

        # transform into state vector
        x[:, 0:3] = SO3.Log_torch(self.C).reshape(-1, 3, 1)
        x[:, 3:6] = v
        x[:, 6:9] = r

        return x, self.C

    def state_jacobian(self, x: torch.Tensor, u: torch.Tensor, dt: float):
        """
        returns the process jacobian with respect to the state at timestep k.

        Parameters
        ----------
        x : torch.Tensor
            The previous state. Expected to be a tensor with shape [N, 9, 1]
        u : torch.Tensor
            The input value. Expected to be a tensor with shape [N, 2, 3]
        dt : float
            The time interval :math:`\Delta t` between the two states, as a float

        Returns
        -------
        F_k : torch.Tensor
            The process jacobian with respect to the state at timestep k, a tensor with shape [N, 15, 15]
        """

        phi = x[:, 0:3]
        C_k = SO3.Exp_torch(phi)

        # retrieve individual IMU measurements
        omega_k = u[:, 0].reshape(-1, 3, 1)
        acc_k = u[:, 1].reshape(-1, 3, 1)

        F_k = torch.eye(15, 15).expand(
            x.shape[0], 15, 15
        )  # torch.eye(3, 3).expand(small_angle_inds.shape[0], 3, 3)

        F_k[:, 6:9, 3:6] = torch.eye(3, 3) * dt
        F_k[:, 3:6, 0:3] = dt * SO3.wedge_torch(-C_k @ acc_k)
        F_k[:, 6:9, 0:3] = (dt**2 / 2) * SO3.wedge_torch(-C_k @ acc_k)
        F_k[:, 0:3, 9:12] = (
            C_k
            @ SO3.Exp_torch(dt * omega_k)
            @ (dt * SO3.left_jacobian_torch(-dt * omega_k))
        )
        F_k[:, 3:6, 12:15] = dt * C_k
        F_k[:, 6:9, 12:15] = (dt**2 / 2) * C_k

        return F_k

    def covariance(
        self, q_c: torch.Tensor, x: torch.Tensor, u: torch.Tensor, dt: float
    ):
        """
        returns the process covariance with embedded noise jacobians at timestep k

        Parameters
        ----------
        x : torch.Tensor
            The previous state. Expected to be a tensor with shape [N, 9, 1]
        u : torch.Tensor
            The input value. Expected to be a tensor with shape [N, 2, 3]
        dt : float
            The time interval :math:`\Delta t` between the two states, as a float

        Returns
        -------
        Q_k : torch.Tensor
            The process covariance, a tensor with shape [N, 15, 15]
        """
        B_k = torch.zeros(15, 12).expand(x.shape[0], 15, 12)

        phi = x[:, 0:3]
        C_k = SO3.Exp_torch(phi)

        # retrieve individual IMU measurements
        omega_k = u[:, 0]

        B_k[:, 0:3, 0:3] = (
            C_k
            @ SO3.Exp_torch(dt * omega_k)
            @ (dt * SO3.left_jacobian_torch((-dt * omega_k).reshape(1, -1)))
        )
        B_k[:, 3:6, 3:6] = dt * C_k
        B_k[:, 6:9, 3:6] = (dt**2 / 2) * C_k
        B_k[:, 9:15, 6:12] = torch.eye(6, 6).expand(x.shape[0], 6, 6)

        # discrete-time covariance (should be a [12x12] tensor)
        q_n = q_c / dt

        # calculate complete process covariance
        Q_k = B_k @ q_n @ B_k.transpose(1, 2)

        return Q_k

class NullOnUpdateCoupledIMU(CoupledIMUKinematicModel):
    def __init__(self, Q_c, perturbation = "right", g_a=torch.tensor([[0], [0], [-scipy.constants.g]])):
        super().__init__(Q_c=Q_c, perturbation=perturbation, g_a=g_a)

        # protect against misshapen g_a vector
        self.g_a = g_a.reshape(1, 3, 1)

        # marker for classification state for each timestep
        self.marker = torch.zeros(5)

    def evaluate(self, x, u, dt):
        """
        Implementation of f(x) for SE_2(3) process model. Propogates state x_{k-1} to x_{k} and returns it

        Parameters
        ----------
        x : torch.Tensor
            The previous state. Expected to be a tensor with shape [N, 5, 5]
        u : torch.Tensor
            The input value. Expected to be a tensor with shape [N, 2, 3]
        dt : float
            The time interval :math:`\Delta t` between the two states, as a float

        Returns
        -------
        x_{k} : torch.Tensor
            The current state - a torch.Tensor with identical dimensions to x_{k-1}
        """

        U = self.generate_u(u, dt)

        G = self.generate_g(u, dt, self.g_a)

        # if angular velocity is null, then nullify upper left corner of RMI propogation matrix
        if (self.marker[0]):
            
            U[:, 0:3, 0:3] = torch.eye(3, 3).unsqueeze(0)

        # if zero-velocity update is occuring, then nullify all propogation corresponding to position and velocity
        if (self.marker[1]):

            U[:, :, 3:] = 0
            U[:, 3:5, 3:5] = torch.eye(2, 2).unsqueeze(0)

            return x @ U

        return G @ x @ U

    def state_jacobian(self, x, u, dt):

        F = super().state_jacobian(x, u ,dt)

        # if angular velocity is null, then nullify and set corresponding entries to identity
        if (self.marker[0]):
            
            # set to zero
            F[:, 0:3, :] = 0

            # set diagonal entries to identity
            F[:, 0:3, 0:3] = torch.eye(3, 3).unsqueeze(0)

        # if zero-velocity update is occuring, then nullify all propogation corresponding to position and velocity
        if (self.marker[1]):

            # set to zero
            F[:, 3:9, :] = 0

            # set diagonal entries corresponding to velocity and position to identity
            F[:, 3:6, 3:6] = torch.eye(3, 3).unsqueeze(0)
            F[:, 6:9, 6:9] = torch.eye(3, 3).unsqueeze(0)

        return F

    def input_jacobian(self, x, u, dt):

        G = super().input_jacobian(x, u, dt)

        # if angular velocity is null, then nullify and set corresponding entries to identity
        if (self.marker[0]):
            
            # set to zero
            G[:, 0:3, :] = 0

        # if zero-velocity update is occuring, then nullify all propogation corresponding to position and velocity
        if (self.marker[1]):

            # set to zero
            G[:, 3:9, :] = 0

        return G
