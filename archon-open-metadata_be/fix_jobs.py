import os
import re

app_java = 'src/main/java/com/archon/openmetadata/ArchonOpenMetadataApplication.java'
with open(app_java, 'r') as f:
    content = f.read()

if '@EnableScheduling' not in content:
    content = content.replace('import org.springframework.scheduling.annotation.EnableAsync;', 'import org.springframework.scheduling.annotation.EnableAsync;\nimport org.springframework.scheduling.annotation.EnableScheduling;')
    content = content.replace('@EnableAsync', '@EnableAsync\n@EnableScheduling')
    with open(app_java, 'w') as f:
        f.write(content)

sim_java = """package com.archon.openmetadata.job.services;

import com.archon.openmetadata.job.models.Job;
import com.archon.openmetadata.job.repositories.JobRepository;
import lombok.RequiredArgsConstructor;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Service;
import java.util.List;

@Service
@RequiredArgsConstructor
public class JobSimulator {
    private final JobRepository repo;

    @Scheduled(fixedRate = 4000)
    public void simulateProgression() {
        List<Job> pending = repo.findAll().stream().filter(j -> "Pending".equals(j.getStatus())).toList();
        for (Job j : pending) {
            j.setStatus("Running");
            repo.save(j);
        }

        List<Job> running = repo.findAll().stream().filter(j -> "Running".equals(j.getStatus())).toList();
        for (Job j : running) {
            // Give it some time before switching to Done (mock logic)
            if (Math.random() > 0.5) {
                j.setStatus("Done");
                repo.save(j);
            }
        }
    }
}
"""
os.makedirs('src/main/java/com/archon/openmetadata/job/services', exist_ok=True)
with open('src/main/java/com/archon/openmetadata/job/services/JobSimulator.java', 'w') as f:
    f.write(sim_java)

