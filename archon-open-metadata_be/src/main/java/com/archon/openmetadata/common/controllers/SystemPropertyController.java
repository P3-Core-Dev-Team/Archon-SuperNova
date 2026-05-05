package com.archon.openmetadata.common.controllers;
import com.archon.openmetadata.common.models.SystemProperty;
import com.archon.openmetadata.common.repositories.SystemPropertyRepository;
import lombok.RequiredArgsConstructor;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;
import java.util.List;

@RestController
@RequestMapping("/api/v1/system-properties")
@RequiredArgsConstructor
@CrossOrigin(origins = "*")
public class SystemPropertyController {
    private final SystemPropertyRepository repo;

    @GetMapping
    public ResponseEntity<List<SystemProperty>> getAll() {
        return ResponseEntity.ok(repo.findAll());
    }

    @PostMapping
    public ResponseEntity<SystemProperty> save(@RequestBody SystemProperty prop) {
        return ResponseEntity.ok(repo.save(prop));
    }
}
