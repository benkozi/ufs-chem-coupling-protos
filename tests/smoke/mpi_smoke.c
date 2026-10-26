/* Prints the communicator size from rank 0; the test expects "ranks=2" from a 2-rank launch. */
#include <mpi.h>
#include <stdio.h>

int main(int argc, char **argv) {
  int rank = -1;
  int size = -1;
  MPI_Init(&argc, &argv);
  MPI_Comm_rank(MPI_COMM_WORLD, &rank);
  MPI_Comm_size(MPI_COMM_WORLD, &size);
  if (rank == 0) {
    printf("ranks=%d\n", size);
  }
  MPI_Finalize();
  return 0;
}
