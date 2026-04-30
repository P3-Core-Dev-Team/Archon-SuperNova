package com.metadata.engine.be.metadata_engine_be;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.scheduling.annotation.EnableAsync;

@SpringBootApplication
@EnableAsync
public class MetadataEngineBeApplication {

	public static void main(String[] args) {
		SpringApplication.run(MetadataEngineBeApplication.class, args);
	}

}
