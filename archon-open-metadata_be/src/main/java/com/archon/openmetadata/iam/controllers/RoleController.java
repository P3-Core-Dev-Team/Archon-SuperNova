package com.archon.openmetadata.iam.controllers;

import com.archon.openmetadata.iam.dto.RoleDto;
import com.archon.openmetadata.iam.dto.RoleFilterBean;
import com.archon.openmetadata.iam.models.Role;
import com.archon.openmetadata.iam.services.RoleService;
import java.util.ArrayList;
import java.util.List;
import java.util.UUID;
import javax.persistence.criteria.Predicate;
import lombok.RequiredArgsConstructor;
import org.modelmapper.ModelMapper;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.domain.Specification;
import org.springframework.data.web.PagedResourcesAssembler;
import org.springframework.hateoas.EntityModel;
import org.springframework.hateoas.PagedModel;
import org.springframework.hateoas.server.mvc.WebMvcLinkBuilder;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/api/v1/roles")
@RequiredArgsConstructor
@CrossOrigin(origins = "*")
public class RoleController {

  private final RoleService service;
  private final ModelMapper modelMapper;
  private final PagedResourcesAssembler<Role> pagedResourcesAssembler;

  @GetMapping
  public ResponseEntity<PagedModel<EntityModel<RoleDto>>> fetchAll(Pageable pageable) {
    Page<Role> page = service.findAll(Specification.where(null), pageable);
    return ResponseEntity.ok(
        pagedResourcesAssembler.toModel(
            page,
            entity ->
                EntityModel.of(
                    modelMapper.map(entity, RoleDto.class),
                    WebMvcLinkBuilder.linkTo(
                            WebMvcLinkBuilder.methodOn(RoleController.class)
                                .getById(entity.getId()))
                        .withSelfRel())));
  }

  @GetMapping("/{id}")
  public ResponseEntity<EntityModel<RoleDto>> getById(@PathVariable UUID id) {
    Role entity = service.findById(id);
    if (entity == null) return ResponseEntity.notFound().build();
    return ResponseEntity.ok(
        EntityModel.of(
            modelMapper.map(entity, RoleDto.class),
            WebMvcLinkBuilder.linkTo(WebMvcLinkBuilder.methodOn(RoleController.class).getById(id))
                .withSelfRel()));
  }

  @PostMapping
  public ResponseEntity<EntityModel<RoleDto>> create(@RequestBody RoleDto dto) {
    Role saved = service.save(modelMapper.map(dto, Role.class));
    return ResponseEntity.ok(
        EntityModel.of(
            modelMapper.map(saved, RoleDto.class),
            WebMvcLinkBuilder.linkTo(
                    WebMvcLinkBuilder.methodOn(RoleController.class).getById(saved.getId()))
                .withSelfRel()));
  }

  @PutMapping("/{id}")
  public ResponseEntity<EntityModel<RoleDto>> update(
      @PathVariable UUID id, @RequestBody RoleDto dto) {
    dto.setId(id);
    Role updated = service.save(modelMapper.map(dto, Role.class));
    return ResponseEntity.ok(
        EntityModel.of(
            modelMapper.map(updated, RoleDto.class),
            WebMvcLinkBuilder.linkTo(
                    WebMvcLinkBuilder.methodOn(RoleController.class).getById(updated.getId()))
                .withSelfRel()));
  }

  @DeleteMapping("/{id}")
  public ResponseEntity<Void> delete(@PathVariable UUID id) {
    service.deleteById(id);
    return ResponseEntity.ok().build();
  }

  @PostMapping("/search")
  public ResponseEntity<PagedModel<EntityModel<RoleDto>>> searchAll(
      @RequestBody RoleFilterBean filterBean, Pageable pageable) {
    Page<Role> page =
        service.findAll(
            (root, query, criteriaBuilder) -> {
              List<Predicate> predicates = new ArrayList<>();

              // Example of inline explicit criteria matching your requirement
              if (filterBean.getSearchText() != null && !filterBean.getSearchText().isEmpty()) {
                // Replace "id" with actual string fields you want to search
                predicates.add(
                    criteriaBuilder.like(
                        root.get("id").as(String.class), "%" + filterBean.getSearchText() + "%"));
              }

              if (predicates.isEmpty()) {
                return criteriaBuilder.conjunction();
              }
              return criteriaBuilder.and(predicates.toArray(new Predicate[0]));
            },
            pageable);

    return ResponseEntity.ok(
        pagedResourcesAssembler.toModel(
            page,
            entity ->
                EntityModel.of(
                    modelMapper.map(entity, RoleDto.class),
                    WebMvcLinkBuilder.linkTo(
                            WebMvcLinkBuilder.methodOn(RoleController.class)
                                .getById(entity.getId()))
                        .withSelfRel())));
  }
}
