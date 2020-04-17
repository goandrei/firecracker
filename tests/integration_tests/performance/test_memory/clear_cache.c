#include <stdlib.h>
#include <time.h>

int main(int argc, char* argv[]) 
{
	unsigned int l3_cache, array_size;
	int *c;

	srand(time(NULL));

	if(argc < 2) {
		l3_cache = 8 * 1024;
	} else {
		l3_cache = atoi(argv[1]);
	}
	
	array_size = l3_cache * 1024;
	c = malloc(sizeof(int) * array_size);
	
	for(int i = 0;i < 500; ++i) {
		for(int j = 0;j < array_size; ++j) {
			c[j] = (i + j) * (rand() % 50); 	
		}
	}

}
