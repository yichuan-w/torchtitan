You are an ML engineer preparing spatial training data for a Gaussian Process surrogate model. Our pipeline computes the Cholesky decomposition of an RBF kernel covariance matrix over a 2D spatial mesh, but it currently crashes with a `LinAlgError` because the matrix is near-singular.

Your task involves several steps to fix this pipeline, implement domain decomposition, and expose the fixed data generator as an HTTP service.

**Step 1: Extract Parameters**
We received a specification snippet as an image located at `/app/spec.png`. Extract the mesh dimension `N` and the Tikhonov regularization parameter `lambda` from this image. 

**Step 2: Matrix Decomposition Fix & Domain Decomposition**
The base grid is an `N x N` mesh defined on the unit square $[0, 1] \times [0, 1]$. Coordinates should be generated using `X, Y = numpy.meshgrid(numpy.linspace(0, 1, N), numpy.linspace(0, 1, N))`.
The covariance between any two points is given by the RBF kernel: $K_{ij} = \exp(-\|p_i - p_j\|^2 / 0.1)$.

To handle the scaling and singularity:
1. Divide the grid points into four quadrants based on their coordinates:
   - `bottom_left`: $x \le 0.5$ and $y \le 0.5$
   - `bottom_right`: $x > 0.5$ and $y \le 0.5$
   - `top_left`: $x \le 0.5$ and $y > 0.5$
   - `top_right`: $x > 0.5$ and $y > 0.5$
2. For each quadrant, compute its local covariance matrix $K_{local}$.
3. Add the regularization parameter `lambda` (extracted from the image) to the diagonal of each $K_{local}$ to make it positive definite: $K_{reg} = K_{local} + \lambda I$.
4. Compute the lower-triangular Cholesky factor $L$ for each regularized quadrant covariance matrix.

**Step 3: Service Deployment**
Create and run an HTTP service (using Flask, FastAPI, or similar) listening on `127.0.0.1:8050`. 
The service must implement the following:
- It must require a Bearer token for all endpoints. The token is: `Bearer gp-data-token-2024`
- Endpoint: `POST /factorize`
  - Request body (JSON): `{"quadrant": "<quadrant_name>"}` where `<quadrant_name>` is one of `bottom_left`, `bottom_right`, `top_left`, `top_right`.
  - Response body (JSON): `{"trace": <float>}` containing the trace (sum of diagonal elements) of the Cholesky factor matrix $L$ for the requested quadrant. Ensure the trace is returned as a standard JSON float.

Keep the service running in the background or foreground so the verifier can test it. You may install any necessary Python packages.