package com.archon.openmetadata.common.controllers;

import com.archon.openmetadata.common.models.SystemAuditLog;
import com.archon.openmetadata.common.repositories.SystemAuditLogRepository;
import lombok.RequiredArgsConstructor;
import org.springframework.data.domain.Sort;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.util.List;

@RestController
@RequestMapping("/api/v1/audits")
@RequiredArgsConstructor
@CrossOrigin(origins = "*")
public class SystemAuditLogController {
    private final SystemAuditLogRepository repo;

    @GetMapping
    public ResponseEntity<List<SystemAuditLog>> getAll() {
        return ResponseEntity.ok(repo.findAll(Sort.by(Sort.Direction.DESC, "timestamp")));
    }
}
